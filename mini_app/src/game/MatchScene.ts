import Phaser from 'phaser';
import type { MatchEvent, NextMatch } from '../api';

/** Event payloads are intentionally tolerant: API versions have used both
 * `type`/`event_type` and `team_id`/`club_id` for the same concepts. */
export type MatchVisualEvent = {
  id?: number;
  match_id?: number;
  sequence?: number;
  minute: number;
  type?: string;
  event_type?: string;
  team_id?: number;
  club_id?: number;
  player_id?: number | null;
  secondary_player_id?: number | null;
  outcome?: string;
  home_score?: number;
  away_score?: number;
  commentary_key?: string;
  description?: string;
  metadata?: unknown;
  coordinates?: { x?: number; y?: number; targetX?: number; targetY?: number } | null;
};

type Side = 'home' | 'away';
type Point = { x: number; y: number };
type PlayerAgent = {
  sprite: Phaser.GameObjects.Arc;
  numberLabel: Phaser.GameObjects.Text;
  side: Side;
  index: number;
  anchor: Point; // normalized formation point, so it survives resizes
  target: Point;
  phase: number;
  runUntil: number;
  runTarget: Point | null;
  nextAutonomousRunAt: number;
  // Fixed-step simulation state; sprites are rendered between these snapshots.
  simX: number;
  simY: number;
  previousX: number;
  previousY: number;
  velocityX: number;
  velocityY: number;
};
type BallFlight = {
  from: Point;
  to: Point;
  /** Follow a moving receiver during a pass instead of aiming at a stale snapshot. */
  toAgent?: PlayerAgent;
  startedAt: number;
  duration: number;
  isGoal: boolean;
  resetToCenter: boolean;
};

const SIM_STEP_MS = 1000 / 60; // 60 Hz is sufficient for smooth football movement on mobile.
const MAX_SIM_STEPS_PER_FRAME = 4;
const TARGET_REFRESH_MS = 280;
const PLAYER_ACCELERATION = 520;
const PLAYER_MAX_SPEED = 125;

export class MatchScene extends Phaser.Scene {
  private homePlayers: PlayerAgent[] = [];
  private awayPlayers: PlayerAgent[] = [];
  // Cache the combined roster; do not allocate a new array every render/simulation step.
  private allPlayers: PlayerAgent[] = [];
  private ball!: Phaser.GameObjects.Arc;
  private processedEventKeys = new Set<string>();
  private pitchGraphics: Phaser.GameObjects.Graphics | null = null;
  private lastWidth = 360;
  private lastHeight = 240;
  private homeClubId: number | null = null;
  private awayClubId: number | null = null;
  private simulationAccumulator = 0;
  private targetRefreshElapsed = 0;
  private simulationTime = 0;
  private visualTime = 0;
  private movementPhase = 0;
  private ballFlight: BallFlight | null = null;
  private ballResetAt: number | null = null;
  private possessionAgent: PlayerAgent | null = null;
  private nextAmbientBallActionAt = 0;
  private ambientPassCount = 0;
  private resizeBound = false;

  constructor() { super('MatchScene'); }

  setTeams(homeClubId: number, awayClubId: number) {
    this.homeClubId = Number(homeClubId) || null;
    this.awayClubId = Number(awayClubId) || null;
  }

  /**
   * Apply a fresh API snapshot without restarting the scene or resetting any
   * sprite positions. Minute and score stay in the React banner; Phaser only
   * needs stable team identities to attribute incoming match events.
   */
  updateMatchData(match: Pick<NextMatch, 'home_club_id' | 'away_club_id'>) {
    this.setTeams(match.home_club_id, match.away_club_id);
  }

  // Phaser calls create once for each scene start. API polling must never call
  // scene.start/restart; updates are handled by updateMatchData/applyEvent.
  create() {
    const { width, height } = this.scale;
    this.lastWidth = Math.max(1, width);
    this.lastHeight = Math.max(1, height);
    this.drawPitch();

    // Compact 4-3-3 formation. The first point is the keeper, then four
    // defenders, three midfielders and three forwards. The away side mirrors it.
    const positions: Point[] = [
      { x: .50, y: .075 },
      { x: .18, y: .205 }, { x: .39, y: .205 }, { x: .61, y: .205 }, { x: .82, y: .205 },
      { x: .29, y: .385 }, { x: .50, y: .405 }, { x: .71, y: .385 },
      { x: .25, y: .575 }, { x: .50, y: .605 }, { x: .75, y: .575 },
    ];
    const mirrored = positions.map(({ x, y }) => ({ x: 1 - x, y: 1 - y }));
    this.homePlayers = this.makeTeam(positions, 'home', width, height, 0x55c7ff);
    this.awayPlayers = this.makeTeam(mirrored, 'away', width, height, 0xff596b);
    this.allPlayers = this.homePlayers.concat(this.awayPlayers);
    this.ball = this.add.circle(width / 2, height / 2, 5, 0xffffff, 1)
      .setStrokeStyle(1, 0x152033).setDepth(4);

    this.scale.off('resize', this.handleResize, this);
    this.scale.on('resize', this.handleResize, this);
    this.resizeBound = true;
    this.resetSimulationClock();
    this.refreshMovementTargets();
    // Kick off a restrained visual passing sequence. Real server events always
    // take priority and replace this cosmetic sequence immediately.
    this.nextAmbientBallActionAt = this.visualTime + 850;

    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      if (this.resizeBound) this.scale.off('resize', this.handleResize, this);
      this.resizeBound = false;
      this.processedEventKeys.clear();
      for (const agent of this.allPlayers) agent.numberLabel.destroy();
      this.allPlayers = [];
      this.homePlayers = [];
      this.awayPlayers = [];
      this.ballFlight = null;
      this.ballResetAt = null;
      this.possessionAgent = null;
    });
  }

  /**
   * Fixed-step movement simulation + frame-rate-independent visual smoothing.
   * Unlike repeatedly retargeted Phaser tweens, a sprite always has one stable
   * desired position and approaches it continuously. Network events only change
   * targets; they never recreate the scene or reset the players to kickoff.
   */
  update(_time: number, delta: number) {
    if (this.scale.width <= 0 || this.scale.height <= 0) return;
    // Use rawDelta when available: Phaser's smoothed delta can hide slow frames.
    const rawDelta = Number((this.game.loop as unknown as { rawDelta?: number }).rawDelta);
    const frameDelta = Number.isFinite(rawDelta) && rawDelta > 0 ? rawDelta : delta;
    const safeDelta = Phaser.Math.Clamp(Number.isFinite(frameDelta) ? frameDelta : 0, 0, 100);
    this.visualTime += safeDelta;
    this.simulationAccumulator += safeDelta;

    let steps = 0;
    while (this.simulationAccumulator >= SIM_STEP_MS && steps < MAX_SIM_STEPS_PER_FRAME) {
      this.advanceSimulation(SIM_STEP_MS);
      this.simulationAccumulator -= SIM_STEP_MS;
      steps += 1;
    }
    // Drop excess backlog after a suspended tab rather than entering a catch-up spiral.
    if (steps === MAX_SIM_STEPS_PER_FRAME) this.simulationAccumulator = Math.min(this.simulationAccumulator, SIM_STEP_MS);

    // Render between the previous and current fixed-step snapshots.
    const alpha = Phaser.Math.Clamp(this.simulationAccumulator / SIM_STEP_MS, 0, 1);
    for (const agent of this.allPlayers) {
      const renderX = Phaser.Math.Linear(agent.previousX, agent.simX, alpha);
      const renderY = Phaser.Math.Linear(agent.previousY, agent.simY, alpha);
      agent.sprite.setPosition(renderX, renderY);
      agent.numberLabel.setPosition(renderX, renderY - 0.5);
    }
    this.updateBallFlight();
    // When the ball is in a player's possession, keep it attached to their
    // moving sprite. Otherwise it would appear to stop dead after each pass.
    if (!this.ballFlight && this.possessionAgent) {
      const holder = this.possessionAgent;
      const sideOffset = holder.side === 'home' ? 8 : -8;
      this.ball.setPosition(holder.sprite.x + sideOffset, holder.sprite.y + 3);
    }
    this.updateAmbientBallAction();
  }

  private resetSimulationClock() {
    this.simulationAccumulator = 0;
    this.targetRefreshElapsed = 0;
    this.simulationTime = 0;
    this.visualTime = 0;
    this.movementPhase = 0;
    this.ambientPassCount = 0;
    this.nextAmbientBallActionAt = 0;
  }

  private makeTeam(anchors: Point[], side: Side, width: number, height: number, _color: number): PlayerAgent[] {
    return anchors.map((anchor, index) => {
      // Modern broadcast-style markers: home team is white/navy, away team is
      // electric blue/white. Shirt numbers remain readable while players move.
      const home = side === 'home';
      const fill = home ? 0xf4f8ff : 0x247bff;
      const outline = home ? 0x3b9bff : 0xf4f8ff;
      const initialX = anchor.x * width;
      const initialY = anchor.y * height;
      const radius = index === 0 ? 10 : 11;
      const sprite = this.add.circle(initialX, initialY, radius, fill, 1)
        .setStrokeStyle(2, outline, 1).setDepth(2);
      const numberLabel = this.add.text(initialX, initialY, String(index === 0 ? 1 : index <= 4 ? [2, 4, 5, 3][index - 1] : index <= 7 ? [6, 8, 10][index - 5] : [7, 9, 11][index - 8]), {
        fontFamily: 'Inter, Arial, sans-serif', fontSize: index === 0 ? '9px' : '10px',
        fontStyle: 'bold', color: home ? '#12385f' : '#ffffff',
        align: 'center',
      }).setOrigin(0.5).setDepth(3);
      return {
        sprite, numberLabel, side, index, anchor: { ...anchor },
        target: { x: initialX, y: initialY },
        phase: index * 1.731 + (side === 'home' ? 0 : Math.PI),
        runUntil: 0,
        runTarget: null,
        // Stagger off-ball runs so the formation is alive even between API events.
        nextAutonomousRunAt: 850 + index * 235 + (side === 'away' ? 420 : 0),
        simX: initialX, simY: initialY,
        previousX: initialX, previousY: initialY,
        velocityX: 0, velocityY: 0,
      };
    });
  }

  private advanceSimulation(stepMs: number) {
    this.simulationTime += stepMs;
    this.targetRefreshElapsed += stepMs;
    if (this.targetRefreshElapsed >= TARGET_REFRESH_MS) {
      this.targetRefreshElapsed %= TARGET_REFRESH_MS;
      this.movementPhase += 1;
      this.refreshMovementTargets();
    }
    const dt = stepMs / 1000;
    for (const agent of this.allPlayers) {
      agent.previousX = agent.simX;
      agent.previousY = agent.simY;
      if (this.visualTime >= agent.runUntil) agent.runTarget = null;
      const target = agent.runTarget && this.visualTime < agent.runUntil ? agent.runTarget : agent.target;
      const dx = target.x - agent.simX;
      const dy = target.y - agent.simY;
      const distance = Math.hypot(dx, dy);
      const desiredSpeed = Math.min(PLAYER_MAX_SPEED, distance * 3.2);
      const desiredVX = distance > 0.01 ? dx / distance * desiredSpeed : 0;
      const desiredVY = distance > 0.01 ? dy / distance * desiredSpeed : 0;
      const maxVelocityChange = PLAYER_ACCELERATION * dt;
      const dvx = desiredVX - agent.velocityX;
      const dvy = desiredVY - agent.velocityY;
      const dv = Math.hypot(dvx, dvy);
      if (dv > maxVelocityChange && dv > 0) {
        agent.velocityX += dvx / dv * maxVelocityChange;
        agent.velocityY += dvy / dv * maxVelocityChange;
      } else {
        agent.velocityX = desiredVX;
        agent.velocityY = desiredVY;
      }
      agent.simX += agent.velocityX * dt;
      agent.simY += agent.velocityY * dt;
      // Settle cleanly at a target instead of jittering around it.
      if (distance < 1.2 && Math.hypot(agent.velocityX, agent.velocityY) < 8) {
        agent.simX = target.x; agent.simY = target.y;
        agent.velocityX = 0; agent.velocityY = 0;
      }
    }
  }

  private refreshMovementTargets() {
    const width = this.scale.width;
    const height = this.scale.height;
    if (width <= 0 || height <= 0) return;

    // The ball is the tactical reference. Teams shift as a unit toward its
    // location instead of oscillating around fixed formation anchors.
    const ballX = Phaser.Math.Clamp((this.ball?.x ?? width / 2) / width, 0.06, 0.94);
    const ballY = Phaser.Math.Clamp((this.ball?.y ?? height / 2) / height, 0.04, 0.96);
    const all = this.allPlayers;

    for (const agent of all) {
      if (this.visualTime < agent.runUntil) continue; // event-driven run has priority
      const { anchor, index, side, phase } = agent;
      // Keep off-ball movement alive between server events. Stagger short,
      // restrained support/pressing runs across players instead of waiting for
      // a pass or shot to trigger every visible movement.
      if (this.visualTime >= agent.nextAutonomousRunAt) {
        const keeper = index === 0;
        if (!keeper) {
          const attackDirection = side === 'home' ? 1 : -1;
          const ballXNow = this.ball.x;
          const ballYNow = this.ball.y;
          const lateral = Math.sin(phase + this.movementPhase * 0.7) * width * 0.075;
          const supportX = Phaser.Math.Clamp(agent.simX + lateral, width * 0.07, width * 0.93);
          const forwardSign = (side === 'home' ? ballYNow > height * 0.48 : ballYNow < height * 0.52) ? 1 : 0.45;
          const runLength = index >= 8 ? height * 0.11 : index >= 5 ? height * 0.075 : height * 0.045;
          agent.runTarget = {
            x: supportX,
            y: Phaser.Math.Clamp(agent.simY + attackDirection * runLength * forwardSign, height * 0.10, height * 0.90),
          };
          agent.runUntil = this.visualTime + 650 + (index % 3) * 130;
        }
        agent.nextAutonomousRunAt = this.visualTime + 1650 + ((index * 173 + (side === 'away' ? 290 : 0)) % 1550);
      }
      const keeper = index === 0;
      const defender = index >= 1 && index <= 4;
      const midfielder = index >= 5 && index <= 7;
      const attackDirection = side === 'home' ? 1 : -1;
      const isAttackingSide = (side === 'home' && ballY > 0.48) || (side === 'away' && ballY < 0.52);

      // Keepers stay goal-side and track the ball laterally. Outfield players
      // preserve their lanes, but move toward the ball and react to possession.
      const ballPullX = keeper ? 0.34 : defender ? 0.18 : midfielder ? 0.25 : 0.22;
      const baseX = anchor.x * width;
      const baseY = anchor.y * height;
      let targetX = baseX + (ballX - anchor.x) * width * ballPullX;
      let targetY = baseY;

      if (keeper) {
        targetX = width * 0.5 + (ballX - 0.5) * width * 0.42;
        targetY = side === 'home' ? height * 0.075 : height * 0.925;
      } else {
        const ballProgress = side === 'home' ? ballY : 1 - ballY;
        const forwardPush = isAttackingSide
          ? (defender ? 0.035 : midfielder ? 0.075 : 0.105)
          : (defender ? -0.012 : midfielder ? -0.025 : -0.035);
        targetY += attackDirection * height * (forwardPush + (ballProgress - 0.5) * (defender ? 0.045 : 0.075));

        // Different lanes prevent the entire side from collapsing onto one point.
        const laneOffset = Math.sin(phase + this.movementPhase * 0.13) * width * (defender ? 0.012 : midfielder ? 0.025 : 0.032);
        targetX += laneOffset;

        // The nearest few players press/support; distant players keep team shape.
        const ballDistance = Math.hypot((baseX - ballX * width) / width, (baseY - ballY * height) / height);
        const proximity = Phaser.Math.Clamp(1 - ballDistance * 1.65, 0, 1);
        targetX += (ballX * width - targetX) * proximity * (midfielder ? 0.20 : defender ? 0.11 : 0.16);
        targetY += (ballY * height - targetY) * proximity * (midfielder ? 0.15 : defender ? 0.08 : 0.12);
      }

      // Slightly vary the desired point over time, but avoid the old large
      // sinusoidal drift that made players wander without reacting to play.
      const microMovement = Math.sin(this.simulationTime / 1100 + phase) * (keeper ? 1 : 2.5);
      agent.target = {
        x: Phaser.Math.Clamp(targetX + microMovement, width * 0.045, width * 0.955),
        y: Phaser.Math.Clamp(targetY, height * (keeper ? 0.035 : 0.10), height * (keeper ? 0.965 : 0.90)),
      };
    }
  }

  private drawPitch() {
    const { width, height } = this.scale;
    this.pitchGraphics?.destroy();
    const g = this.add.graphics().setDepth(0);
    // A clean broadcast-style pitch: deep emerald base, subtle alternating
    // mowing bands, inset touchlines and restrained high-contrast markings.
    g.fillStyle(0x062d2b, 1);
    g.fillRoundedRect(0, 0, width, height, 18);
    const pad = Math.max(9, width * 0.035);
    const fieldX = pad;
    const fieldY = pad;
    const fieldW = width - pad * 2;
    const fieldH = height - pad * 2;
    g.fillStyle(0x0b6548, 1);
    g.fillRoundedRect(fieldX, fieldY, fieldW, fieldH, 11);
    const stripeH = fieldH / 10;
    for (let i = 0; i < 10; i++) {
      if (i % 2 === 0) {
        g.fillStyle(0x0e7552, 0.62);
        g.fillRect(fieldX + 1, fieldY + i * stripeH, fieldW - 2, stripeH);
      }
    }
    g.lineStyle(1.35, 0xc8f7e8, 0.82);
    g.strokeRoundedRect(fieldX + 7, fieldY + 7, fieldW - 14, fieldH - 14, 4);
    g.lineBetween(width / 2, fieldY + 7, width / 2, height - fieldY - 7);
    g.strokeCircle(width / 2, height / 2, Math.min(fieldW, fieldH) * 0.105);
    g.fillStyle(0xe6fff5, 0.9);
    g.fillCircle(width / 2, height / 2, 2.2);
    const boxW = fieldW * 0.48;
    const boxX = (width - boxW) / 2;
    const boxH = fieldH * 0.155;
    g.strokeRect(boxX, fieldY + 7, boxW, boxH);
    g.strokeRect(boxX, height - fieldY - 7 - boxH, boxW, boxH);
    const sixW = fieldW * 0.24;
    const sixX = (width - sixW) / 2;
    const sixH = fieldH * 0.06;
    g.strokeRect(sixX, fieldY + 7, sixW, sixH);
    g.strokeRect(sixX, height - fieldY - 7 - sixH, sixW, sixH);
    // Goal frames and penalty spots give the pitch a more finished look.
    g.lineStyle(2.5, 0xf2fffb, 0.96);
    g.strokeRoundedRect(width * 0.425, 2, width * 0.15, 9, 2);
    g.strokeRoundedRect(width * 0.425, height - 11, width * 0.15, 9, 2);
    g.fillStyle(0xf2fffb, 0.85);
    g.fillCircle(width / 2, fieldY + 7 + boxH * 0.72, 1.8);
    g.fillCircle(width / 2, height - fieldY - 7 - boxH * 0.72, 1.8);
    this.pitchGraphics = g;
  }

  private handleResize() {
    const width = this.scale.width;
    const height = this.scale.height;
    if (width <= 0 || height <= 0) return;
    const oldWidth = Math.max(1, this.lastWidth || width);
    const oldHeight = Math.max(1, this.lastHeight || height);
    const allAgents = [...this.homePlayers, ...this.awayPlayers];
    for (const agent of allAgents) {
      const sx = width / oldWidth;
      const sy = height / oldHeight;
      agent.sprite.setPosition(agent.sprite.x * sx, agent.sprite.y * sy);
      agent.target = { x: agent.target.x * sx, y: agent.target.y * sy };
      agent.simX *= sx; agent.previousX *= sx; agent.velocityX *= sx;
      agent.simY *= sy; agent.previousY *= sy; agent.velocityY *= sy;
      if (agent.runTarget) agent.runTarget = { x: agent.runTarget.x * sx, y: agent.runTarget.y * sy };
    }
    if (this.ball) this.ball.setPosition(this.ball.x / oldWidth * width, this.ball.y / oldHeight * height);
    if (this.ballFlight) {
      this.ballFlight.from = { x: this.ballFlight.from.x / oldWidth * width, y: this.ballFlight.from.y / oldHeight * height };
      this.ballFlight.to = { x: this.ballFlight.to.x / oldWidth * width, y: this.ballFlight.to.y / oldHeight * height };
    }
    this.lastWidth = width;
    this.lastHeight = height;
    this.drawPitch();
    this.refreshMovementTargets();
  }

  private getEventKey(event: MatchVisualEvent | MatchEvent, normalized: MatchVisualEvent): string {
    const id = Number((event as MatchEvent).id ?? normalized.sequence ?? 0);
    if (Number.isFinite(id) && id > 0) return `id:${id}`;
    return JSON.stringify({
      match: normalized.match_id ?? 0,
      minute: normalized.minute ?? 0,
      type: normalized.type ?? normalized.event_type ?? '',
      team: normalized.team_id ?? normalized.club_id ?? 0,
      player: normalized.player_id ?? 0,
      secondary: normalized.secondary_player_id ?? 0,
      description: normalized.description ?? normalized.commentary_key ?? '',
      coordinates: normalized.coordinates ?? normalized.metadata ?? null,
    });
  }

  applyEvent(event: MatchVisualEvent | MatchEvent) {
    const normalized = event as MatchVisualEvent & MatchEvent;
    const key = this.getEventKey(event, normalized);
    if (this.processedEventKeys.has(key)) return;
    this.processedEventKeys.add(key);
    if (this.processedEventKeys.size > 600) {
      const oldest = this.processedEventKeys.values().next().value as string | undefined;
      if (oldest) this.processedEventKeys.delete(oldest);
    }

    const kind = String(normalized.type ?? normalized.event_type ?? '').trim().toUpperCase();
    const seed = Number((event as MatchEvent).id ?? normalized.sequence ?? normalized.minute ?? 0);
    const width = this.scale.width;
    const height = this.scale.height;
    const teamId = Number(normalized.team_id ?? normalized.club_id ?? this.homeClubId ?? 0);
    const isAway = this.awayClubId !== null && teamId === this.awayClubId;
    const agents = isAway ? this.awayPlayers : this.homePlayers;
    const opponents = isAway ? this.homePlayers : this.awayPlayers;
    if (!agents.length) return;
    const playerIndex = Math.abs(Number(normalized.player_id ?? seed)) % agents.length;
    const player = agents[playerIndex];
    const metadata = normalized.metadata && typeof normalized.metadata === 'object'
      ? normalized.metadata as Record<string, unknown> : {};
    const coordinates = (normalized.coordinates ?? metadata.coordinates ?? metadata) as Record<string, unknown> | null;
    const isGoal = kind.includes('GOAL');
    const isShot = kind.includes('SHOT') || isGoal;
    const isPass = kind.includes('PASS') || kind.includes('CROSS');
    const isCarry = kind.includes('CARRY') || kind.includes('DRIBBLE');
    const isPressure = kind.includes('PRESSURE');
    const isSave = kind.includes('SAVE');
    const isTackle = kind.includes('TACKLE') || kind.includes('INTERCEPTION') || kind.includes('STEAL');
    const isAction = isPass || isCarry || isShot || kind.includes('CORNER') || isSave || isTackle || isPressure || kind.includes('OFFSIDE') || kind.includes('INJURY');
    const direction = isAway ? -1 : 1;
    const ownGoalY = isAway ? height * .93 : height * .07;
    const attackGoalY = isAway ? height * .07 : height * .93;
    const playerStart = { x: player.sprite.x, y: player.sprite.y };

    const readCoordinate = (value: unknown, size: number, fallback: number) => {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      if (number >= 0 && number <= 1) return number * size;
      if (number >= 0 && number <= 100) return number / 100 * size;
      return Phaser.Math.Clamp(number, 12, size - 12);
    };

    let receiver: PlayerAgent | null = null;
    if (isPass) {
      const receiverIndex = normalized.secondary_player_id != null
        ? Math.abs(Number(normalized.secondary_player_id)) % agents.length
        : (playerIndex + 3) % agents.length;
      receiver = agents[receiverIndex];
      if (receiver === player && agents.length > 1) receiver = agents[(receiverIndex + 1) % agents.length];
    }

    const explicitX = coordinates?.targetX ?? (isGoal ? undefined : coordinates?.x);
    const explicitY = coordinates?.targetY ?? (isGoal ? undefined : coordinates?.y);
    let target: Point;
    if (isGoal) {
      // A goal event always finishes in the attacking goal, even if the event
      // metadata contains the shooter's coordinates rather than the goal point.
      target = { x: width * (.44 + (seed % 4) * .04), y: attackGoalY };
    } else if (isPass && receiver && explicitX == null && explicitY == null) {
      target = { x: receiver.sprite.x, y: receiver.sprite.y };
    } else {
      target = {
        x: readCoordinate(explicitX, width, isShot ? width / 2 : Phaser.Math.Clamp(playerStart.x + direction * 30, width * .15, width * .85)),
        y: readCoordinate(explicitY, height, isShot ? attackGoalY : Phaser.Math.Clamp(playerStart.y + direction * 24, height * .12, height * .88)),
      };
    }
    target.x = Phaser.Math.Clamp(target.x, 12, width - 12);
    target.y = Phaser.Math.Clamp(target.y, 12, height - 12);


    if (kind.includes('YELLOW') || kind.includes('RED') || kind.includes('CARD')) {
      const color = kind.includes('RED') ? 0xff243b : 0xffd43b;
      const card = this.add.rectangle(playerStart.x, playerStart.y - 22, 9, 13, color).setDepth(8);
      this.tweens.add({ targets: card, y: card.y - 12, alpha: 0, duration: 1200, onComplete: () => card.destroy() });
      player.sprite.setScale(1.15);
      this.tweens.add({ targets: player.sprite, scaleX: 1, scaleY: 1, duration: 260, ease: 'Sine.easeOut' });
      return;
    }
    if (!isAction) return;
    this.nextAmbientBallActionAt = this.visualTime + 1500;

    // A runner temporarily overrides its normal formation target. No tween is
    // stacked or repeatedly killed: the normal spring resumes after the run.
    const sprintDistance = isShot ? height * .13 : height * .075;
    player.runTarget = {
      x: Phaser.Math.Clamp(isGoal || isShot ? target.x : playerStart.x + Math.sin(seed + playerIndex) * width * .035, 14, width - 14),
      y: Phaser.Math.Clamp(isGoal || isShot ? attackGoalY - direction * 8 : playerStart.y + direction * sprintDistance, 14, height - 14),
    };
    player.runUntil = this.visualTime + (isShot ? 1150 : 900);

    if (isCarry) {
      this.possessionAgent = player;
      this.ballFlight = null;
      this.ballResetAt = null;
    }

    // Tactical reactions: the receiver attacks the passing lane, defenders
    // close the ball carrier, and the keeper sets up on the shot line.
    if ((isPass || isCarry) && receiver) {
      receiver.runTarget = {
        x: Phaser.Math.Clamp(receiver.sprite.x + direction * width * 0.025, width * 0.06, width * 0.94),
        y: Phaser.Math.Clamp(receiver.sprite.y + direction * height * 0.025, height * 0.08, height * 0.92),
      };
      receiver.runUntil = this.visualTime + 650;
    }
    if (isShot) {
      const keeper = opponents[0];
      if (keeper) {
        keeper.runTarget = {
          x: Phaser.Math.Clamp(target.x, width * 0.40, width * 0.60),
          y: keeper.side === 'home' ? height * 0.075 : height * 0.925,
        };
        keeper.runUntil = this.visualTime + 1000;
        this.tweens.add({ targets: keeper.sprite, scaleX: 1.25, scaleY: 1.25, duration: 180, yoyo: true, repeat: 1 });
      }
      // Defenders nearest to the shooter recover toward the goal-side lane.
      opponents.slice(1, 5).forEach((defender) => {
        defender.runTarget = {
          x: Phaser.Math.Clamp(defender.sprite.x + (width / 2 - defender.sprite.x) * 0.18, width * 0.08, width * 0.92),
          y: Phaser.Math.Clamp(defender.sprite.y + (target.y - defender.sprite.y) * 0.12, height * 0.08, height * 0.92),
        };
        defender.runUntil = this.visualTime + 700;
      });
    }
    if (isTackle) {
      this.possessionAgent = player;
      opponents.forEach((opponent) => {
        const distance = Math.hypot(opponent.sprite.x - player.sprite.x, opponent.sprite.y - player.sprite.y);
        if (distance < height * 0.28) {
          opponent.runTarget = { x: player.sprite.x, y: player.sprite.y };
          opponent.runUntil = this.visualTime + 500;
        }
      });
    }

    let ballTarget = target;
    if (isPass && receiver && explicitX == null && explicitY == null) {
      ballTarget = { x: receiver.sprite.x, y: receiver.sprite.y };
    }
    this.ballFlight = {
      from: { x: this.ball.x, y: this.ball.y },
      to: { x: ballTarget.x, y: ballTarget.y },
      toAgent: isPass && receiver && explicitX == null && explicitY == null ? receiver : undefined,
      startedAt: this.visualTime,
      duration: isPass ? 720 : isShot ? 880 : 650,
      isGoal,
      resetToCenter: isGoal || isShot || isSave,
    };
    // Possession changes only when the pass reaches its receiver; shots and
    // saves release possession until the next real match event.
    this.possessionAgent = isShot || isSave ? null : (isPass && receiver ? receiver : player);
    this.ballResetAt = null;
    this.refreshMovementTargets();
  }

  /**
   * Matches currently stream discrete persisted highlights rather than 2D
   * coordinates on every frame. Keep the ball alive between those real events
   * with a slow visual passing sequence; it never changes server score/stats.
   */
  private updateAmbientBallAction() {
    if (this.visualTime < this.nextAmbientBallActionAt || this.ballFlight || this.ballResetAt !== null) return;
    const width = this.scale.width;
    const height = this.scale.height;
    if (width <= 0 || height <= 0 || this.homePlayers.length === 0 || this.awayPlayers.length === 0) return;

    // Start from a central midfielder when there has not been a visual action yet.
    if (!this.possessionAgent) {
      const kickoffTeam = this.ambientPassCount % 2 === 0 ? this.homePlayers : this.awayPlayers;
      const receiver = kickoffTeam[6] ?? kickoffTeam[5] ?? kickoffTeam[1];
      if (!receiver) return;
      this.possessionAgent = receiver;
      const duration = 540;
      this.ballFlight = {
        from: { x: this.ball.x, y: this.ball.y },
        to: { x: receiver.sprite.x, y: receiver.sprite.y },
        toAgent: receiver,
        startedAt: this.visualTime,
        duration,
        isGoal: false,
        resetToCenter: false,
      };
      this.nextAmbientBallActionAt = this.visualTime + duration + 420;
      return;
    }

    const holder = this.possessionAgent;
    this.ambientPassCount += 1;
    const ownTeam = holder.side === 'home' ? this.homePlayers : this.awayPlayers;
    const opponentTeam = holder.side === 'home' ? this.awayPlayers : this.homePlayers;
    // Most touches are short passes to teammates. Occasionally let the other
    // side recover possession to keep both formations engaged, without emitting
    // a fabricated event into the real match commentary/history.
    const turnover = this.ambientPassCount % 7 === 0;
    const candidates = (turnover ? opponentTeam : ownTeam).filter(agent => agent.index !== 0 && agent !== holder);
    if (!candidates.length) return;
    const pick = Math.abs(holder.index * 5 + this.ambientPassCount * 3) % candidates.length;
    const receiver = candidates[pick];
    const holderDirection = holder.side === 'home' ? 1 : -1;
    const receiverDirection = receiver.side === 'home' ? 1 : -1;
    const runLength = height * (turnover ? 0.02 : 0.035);

    holder.runTarget = {
      x: Phaser.Math.Clamp(holder.sprite.x + (turnover ? -1 : 1) * holderDirection * width * 0.018, width * 0.07, width * 0.93),
      y: Phaser.Math.Clamp(holder.sprite.y + holderDirection * height * 0.018, height * 0.08, height * 0.92),
    };
    holder.runUntil = this.visualTime + 500;
    receiver.runTarget = {
      x: Phaser.Math.Clamp(receiver.sprite.x + receiverDirection * width * 0.015, width * 0.06, width * 0.94),
      y: Phaser.Math.Clamp(receiver.sprite.y + receiverDirection * runLength, height * 0.08, height * 0.92),
    };
    receiver.runUntil = this.visualTime + 650;

    const duration = 620 + (this.ambientPassCount % 4) * 85;
    this.ballFlight = {
      from: { x: this.ball.x, y: this.ball.y },
      to: { x: receiver.sprite.x, y: receiver.sprite.y },
      toAgent: receiver,
      startedAt: this.visualTime,
      duration,
      isGoal: false,
      resetToCenter: false,
    };
    this.possessionAgent = receiver;
    this.ballResetAt = null;
    this.nextAmbientBallActionAt = this.visualTime + duration + 350;
    this.refreshMovementTargets();
  }

  private updateBallFlight() {
    if (this.ballFlight) {
      const flight = this.ballFlight;
      const rawProgress = Phaser.Math.Clamp((this.visualTime - flight.startedAt) / flight.duration, 0, 1);
      const eased = rawProgress * rawProgress * (3 - 2 * rawProgress);
      // Re-read the receiver's position on every frame so passes land on the
      // player's current location, not where the player was when the pass began.
      const destination = flight.toAgent
        ? { x: flight.toAgent.sprite.x, y: flight.toAgent.sprite.y }
        : flight.to;
      this.ball.setPosition(
        Phaser.Math.Linear(flight.from.x, destination.x, eased),
        Phaser.Math.Linear(flight.from.y, destination.y, eased),
      );
      if (rawProgress >= 1) {
        this.ballFlight = null;
        if (flight.resetToCenter) this.possessionAgent = null;
        this.refreshMovementTargets();
        if (flight.isGoal) {
          this.cameras.main.flash(240, 150, 255, 180);
          this.ballResetAt = this.visualTime + 850;
        } else if (flight.resetToCenter) {
          this.ballResetAt = this.visualTime + 300;
        }
      }
      return;
    }

    if (this.ballResetAt !== null && this.visualTime >= this.ballResetAt) {
      this.ballResetAt = null;
      this.possessionAgent = null;
      this.ballFlight = {
        from: { x: this.ball.x, y: this.ball.y },
        to: { x: this.scale.width / 2, y: this.scale.height / 2 },
        startedAt: this.visualTime,
        duration: 450,
        isGoal: false,
        resetToCenter: false,
      };
    }
  }
}
