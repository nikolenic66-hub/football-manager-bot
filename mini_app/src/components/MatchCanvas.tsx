import React, { memo, useEffect, useRef, useState } from 'react';
import Phaser from 'phaser';
import { MatchScene } from '../game/MatchScene';
import type { MatchEvent, NextMatch } from '../api';

type MatchCanvasProps = { match: NextMatch | null };

function eventKey(event: MatchEvent): string {
  if (Number(event.id) > 0) return `id:${event.id}`;
  return JSON.stringify({
    minute: event.minute,
    type: event.event_type,
    club: event.club_id ?? 0,
    player: event.player_id ?? 0,
    receiver: (event as MatchEvent & { secondary_player_id?: number | null }).secondary_player_id ?? 0,
    description: event.description ?? '',
    coordinates: (event as MatchEvent & { coordinates?: unknown }).coordinates ?? null,
    metadata: event.metadata ?? null,
  });
}

function orderedEvents(events: MatchEvent[]): MatchEvent[] {
  return [...events].sort((a, b) => {
    const minuteDelta = Number(a.minute ?? 0) - Number(b.minute ?? 0);
    if (minuteDelta !== 0) return minuteDelta;
    return Number(a.id ?? 0) - Number(b.id ?? 0);
  });
}

function MatchCanvas({ match }: MatchCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const gameRef = useRef<Phaser.Game | null>(null);
  const sceneRef = useRef<MatchScene | null>(null);
  const matchRef = useRef<NextMatch | null>(match);
  const seenEventKeysRef = useRef<Set<string>>(new Set());
  const initializedEventsForMatchRef = useRef(false);
  const activeMatchIdRef = useRef<number | null>(match?.id ?? null);
  const [error, setError] = useState('');
  const matchId = match?.id ?? null;
  const isLive = String(match?.status ?? '').trim().toUpperCase() === 'LIVE';

  // Keep the newest API snapshot available to Phaser's asynchronous startup.
  matchRef.current = match;
  if (matchId !== activeMatchIdRef.current) {
    activeMatchIdRef.current = matchId;
    seenEventKeysRef.current = new Set();
    initializedEventsForMatchRef.current = false;
  }

  const syncMatchToScene = (scene: MatchScene, currentMatch: NextMatch) => {
    scene.updateMatchData(currentMatch);
    const events = orderedEvents(currentMatch.events ?? []);

    // On first mount, historical events should not replay a whole match in one
    // frame. Mark older events as seen and animate only the latest one. Every
    // genuinely new event arriving from the API is animated once afterwards.
    if (!initializedEventsForMatchRef.current) {
      for (const oldEvent of events.slice(0, -1)) {
        seenEventKeysRef.current.add(eventKey(oldEvent));
      }
      const latest = events[events.length - 1];
      if (latest) {
        scene.applyEvent(latest as MatchEvent);
        seenEventKeysRef.current.add(eventKey(latest));
      }
      initializedEventsForMatchRef.current = true;
      return;
    }

    for (const event of events) {
      const key = eventKey(event);
      if (seenEventKeysRef.current.has(key)) continue;
      // Mark before dispatch to protect against duplicate snapshots in the same
      // render cycle. MatchScene has its own guard as a second safety layer.
      seenEventKeysRef.current.add(key);
      scene.applyEvent(event as MatchEvent);
    }
    if (seenEventKeysRef.current.size > 800) {
      // Keep the set bounded for long-running LIVE matches. The scene has its
      // own bounded event-key cache too.
      seenEventKeysRef.current = new Set(Array.from(seenEventKeysRef.current).slice(-500));
    }
  };

  useEffect(() => {
    const scene = sceneRef.current;
    const currentMatch = matchRef.current;
    if (scene && currentMatch) syncMatchToScene(scene, currentMatch);
  }, [match]);

  useEffect(() => {
    if (!hostRef.current || !isLive || matchId === null) return;
    // The Phaser instance belongs to this match ID. Snapshot refreshes update the
    // existing scene via the other effect and must never recreate this instance.
    let game: Phaser.Game | null = gameRef.current;
    let attachTimer: number | null = null;
    let disposed = false;

    const attachScene = () => {
      if (disposed || !game || sceneRef.current) return;
      try {
        const scene = game.scene.getScene('MatchScene') as MatchScene;
        if (scene && (scene as any).sys?.isActive?.()) {
          sceneRef.current = scene;
          const latestMatch = matchRef.current;
          if (latestMatch) syncMatchToScene(scene, latestMatch);
          if (attachTimer !== null) {
            window.clearInterval(attachTimer);
            attachTimer = null;
          }
          setError('');
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Не удалось отобразить матч');
      }
    };

    try {
      // A guard also protects against duplicate effect setup. In normal operation
      // this instance is created once for the current LIVE match.
      if (!game) {
        game = new Phaser.Game({
          type: Phaser.AUTO,
          parent: hostRef.current,
          width: 360,
          height: 240,
          backgroundColor: '#073d30',
          scale: { mode: Phaser.Scale.RESIZE, autoCenter: Phaser.Scale.CENTER_BOTH },
          // Render at 60 FPS where the device supports it; fixed-step simulation and
          // interpolation keep player movement smooth between simulation ticks.
          fps: { target: 60, forceSetTimeOut: false, smoothStep: true },
          render: { antialias: true, pixelArt: false, roundPixels: false },
          scene: [MatchScene],
        });
        gameRef.current = game;
      }
      attachTimer = window.setInterval(attachScene, 50);
      attachScene();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось запустить 2D-движок матча');
    }

    return () => {
      disposed = true;
      if (attachTimer !== null) window.clearInterval(attachTimer);
      if (game && gameRef.current === game) {
        gameRef.current = null;
        game.destroy(true);
      }
      sceneRef.current = null;
    };
  }, [isLive, matchId]);

  if (!match || !isLive) return null;

  return (
    <div className="match-canvas-wrap">
      <div className="live-match-banner">
        <span className="live-dot" /> LIVE <strong>{match.current_minute ?? 0}′</strong>
        <span>{match.home_name} {match.home_score}:{match.away_score} {match.away_name}</span>
      </div>
      <div ref={hostRef} className="match-canvas" aria-label="2D-анимация текущего матча" />
      {error && <p className="api-error">{error}</p>}
    </div>
  );
}


/**
 * Polling may return a fresh object every five seconds even when the meaningful
 * match snapshot has not changed. Avoid needless React renders in that case.
 * If minute/score/team/event data changes, render normally; Phaser still stays
 * alive because its creation effect depends only on LIVE state and match ID.
 */
function areMatchCanvasPropsEqual(previous: MatchCanvasProps, next: MatchCanvasProps): boolean {
  const a = previous.match;
  const b = next.match;
  if (a === b) return true;
  if (!a || !b) return a === b;
  if (
    a.id !== b.id || a.status !== b.status ||
    a.current_minute !== b.current_minute ||
    a.home_club_id !== b.home_club_id || a.away_club_id !== b.away_club_id ||
    a.home_name !== b.home_name || a.away_name !== b.away_name ||
    a.home_score !== b.home_score || a.away_score !== b.away_score
  ) return false;
  const eventsA = orderedEvents(a.events ?? []);
  const eventsB = orderedEvents(b.events ?? []);
  if (eventsA.length !== eventsB.length) return false;
  for (let i = 0; i < eventsA.length; i++) {
    if (eventKey(eventsA[i]) !== eventKey(eventsB[i])) return false;
  }
  return true;
}

export default memo(MatchCanvas, areMatchCanvasPropsEqual);
