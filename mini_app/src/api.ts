export type Club = {
  id: number; name: string; short_name?: string; budget: number;
  stadium_name?: string; stadium_level?: number; fans?: number; reputation?: number;
  squad_size?: number; emblem_shape?: string; emblem_color?: string; emblem_symbol?: string;
  league_id?: number | null; league_name?: string | null; division_code?: string | null; division_name?: string | null;
};
export type Player = {
  id: number; first_name: string; last_name: string; position: string; overall: number;
  fitness: number; morale: number; is_injured: boolean; portrait_url?: string | null; age?: number;
};
export type MatchEvent = { id: number; minute: number; event_type: string; club_id?: number; player_id?: number; description: string; metadata?: unknown; commentary?: string | null; ai_commentary?: string | null; comment?: string | null };
export type LiveMatch = { id: number; home_club_id: number; away_club_id: number; home_name: string; away_name: string; home_score: number; away_score: number; status: string; current_minute?: number | null; competition_type?: string; is_test?: boolean; events: MatchEvent[]; scheduled_at?: string | null; started_at?: string | null; competition_name?: string | null };
export type NextMatch = LiveMatch & { started_at?: string | null; scheduled_at?: string | null; competition_name?: string | null };
export type LastMatch = { id:number; home_club_id:number; away_club_id:number; home_name:string; away_name:string; home_score:number; away_score:number; status:string; started_at?:string|null; finished_at?:string|null; scheduled_at?:string|null; competition_type?:string|null; competition_name?:string|null };
export const getLastMatch = () => api<{ match: LastMatch | null }>('/api/matches/last');
export type LeagueTableRow = { id: number; name: string; played: number; wins: number; draws: number; losses: number; goals_for: number; goals_against: number; points: number };
export type LeagueResponse = { league_id?: number | null; league_name?: string | null; division_code?: string | null; division_name?: string | null; table: LeagueTableRow[] };

export const TELEGRAM_REQUIRED_MESSAGE = 'Откройте приложение из Telegram';

function initData(): string {
  const tg = (window as any).Telegram?.WebApp;
  return typeof tg?.initData === 'string' ? tg.initData.trim() : '';
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const telegramInitData = initData();
  // Do not send unauthenticated API requests when opened in a normal browser.
  if (!telegramInitData) throw new Error(TELEGRAM_REQUIRED_MESSAGE);

  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  headers.set('X-Telegram-Init-Data', telegramInitData);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  let response: Response;
  try { response = await fetch(path, { ...options, headers }); }
  catch { throw new Error('Не удалось связаться с сервером. Проверь интернет и попробуй снова.'); }
  if (!response.ok) {
    let message = `Ошибка API (${response.status})`;
    try { const data = await response.json(); if (data?.detail) message = String(data.detail); } catch { /* non-JSON error */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const getClub = (clubId: number) => api<Club>(`/api/club/${clubId}`);
export const getSquad = (clubId: number) => api<{ players: Player[] }>(`/api/squad/${clubId}`);
export const getLiveMatches = () => api<{ matches: LiveMatch[] }>('/api/matches/live');
export const getNextMatch = () => api<{ match: NextMatch | null }>('/api/matches/next');
export const getLiveMatchLineup = (matchId: number) => api<{ match_id: number; club_id: number; starting_ids: number[]; bench_ids: number[] }>(`/api/matches/${matchId}/lineup`);
export const substituteLivePlayer = (matchId: number, playerOutId: number, playerInId: number) =>
  api<{ status: string; match_id: number; minute: number; player_out_id: number; player_in_id: number; description: string; substitutions_used: number }>(
    `/api/matches/${matchId}/substitute`,
    { method: 'POST', body: JSON.stringify({ player_out_id: playerOutId, player_in_id: playerInId }) },
  );
export const updateLiveMatchTactics = (matchId: number, data: Record<string, string | number>) =>
  api<{ status: string; match_id: number; formation: string; style: string; tempo: number; pressing: number; width: number; defensive_line: number; aggression: number }>(
    `/api/matches/${matchId}/tactics`,
    { method: 'POST', body: JSON.stringify(data) },
  );
export const getTactics = (clubId: number) => api<Record<string, string | number>>(`/api/tactics/${clubId}`);
export const saveTactics = (clubId: number, data: Record<string, string | number>) => api(`/api/tactics/${clubId}`, { method: 'POST', body: JSON.stringify(data) });
export const bidForPlayer = (playerId: number) => api<{ status: string; player_id: number; price: number; offer_id?: number }>('/api/transfers/bid', { method: 'POST', body: JSON.stringify({ player_id: playerId }) });
export const getLeague = () => api<LeagueResponse>('/api/league');

export type Me = { telegram_id: number; user_id: number; club_id: number | null; club_name: string | null; first_name?: string | null; username?: string | null; avatar_id?: number };
export const getMe = () => api<Me>("/api/me");
export type CreateClubInput = { name: string; emblem_shape: string; emblem_color: string; emblem_symbol: string };
export const createClub = (data: CreateClubInput) => api<{ status: string; club_id: number; name: string; emblem_shape: string; emblem_color: string; emblem_symbol: string }>("/api/club/create", { method: "POST", body: JSON.stringify(data) });

export type MarketPlayer = {
  id: number; first_name: string; last_name: string; position: string; age: number;
  market_value: number; potential: number; overall?: number; nationality?: string;
  rarity?: string; seller_club_id?: number | null; listing_id?: number | null;
  asking_price: number; minimum_price?: number; is_listed?: boolean;
};
export type TransferListingsResponse = { club_id: number; listings: MarketPlayer[] };
export type ScoutRecommendationsResponse = { recommendations: MarketPlayer[] };
export type Fixture = { id: number; round: number; home_club_id: number; away_club_id: number; home_name: string; away_name: string; home_score: number | null; away_score: number | null; status: string; scheduled_at?: string | null; competition_type?: string | null; comp_type?: string | null; competition_name?: string | null };
export type FixturesResponse = { fixtures: Fixture[] };
export type TrainingToday = { focus: string; intensity: string; last_training_at?: string | null; squad_size: number; avg_fitness: number };
export type AcademyYouth = { id: number; first_name: string; last_name: string; position: string; age: number; potential: number; overall: number; rarity?: string };
export type LineupPlayer = Player & { form?: number; suspension_matches?: number; pace?: number; shooting?: number; passing?: number; dribbling?: number; defending?: number; physical?: number; stamina?: number; mental?: number };
export type FormationSlot = { slot: string; x: number; y: number };
export type StartingLineup = { formation: string; formation_slots: FormationSlot[]; players: LineupPlayer[]; starting_ids: number[]; bench_ids?: number[] };
export type PackOpeningResult = { status: string; pack_id: number; pack_name: string; player_id: number; template_id: number; name: string; position: string; ovr: number; rarity: string; price_coins: number; age?: number; nationality?: string | null; potential?: number; portrait_url?: string | null; card_version?: string | null };
export type PlayedFriendlyMatch = { status: string; mode: 'friendly'|'test'; match_id: number; home_club_id: number; away_club_id: number; home_name: string; away_name: string; home_score: number; away_score: number; minute: number; competition_name: string };
export type LineupSaveResponse = { status: string; starting_ids: number[]; bench_ids?: number[]; formation?: string };
export type ManagerProfileResponse = { status: string; manager_name: string };
export type ClubNameResponse = { status: string; club_id: number; name: string };
export type ManagerAvatarResponse = { status: string; avatar_id: number };
export type EmblemUpdateResponse = { status: string; emblem_shape: string; emblem_color: string; emblem_symbol: string };
export type Pack = { id: number; name: string; price_coins: number; price_stars: number; min_ovr: number; max_ovr: number };
export type StadiumInfo = { name: string; level: number; capacity: number; fans: number; ticket_price: number; upgrade_cost: number | null; budget: number };
export const getTransferListings = () => api<TransferListingsResponse>('/api/transfers/listings');
export const getScoutRecommendations = () => api<ScoutRecommendationsResponse>('/api/scout/recommendations');
export const getFixtures = () => api<FixturesResponse>('/api/league/fixtures');
export const getTrainingToday = () => api<TrainingToday>('/api/training/today');
export const completeTraining = (focus = 'BALANCED', intensity = 'NORMAL') => api<{ status: string; remaining_seconds?: number; players_trained: number; batch_id?: string | null }>('/api/training/complete', { method: 'POST', body: JSON.stringify({ focus, intensity }) });
export const getAcademyYouth = () => api<{ youth: AcademyYouth[]; academy_level?: number; max_overall?: number; capacity?: number }>('/api/academy/youth');
export const promoteYouth = (id: number) => api<{ status: string; player_id: number; name: string }>('/api/academy/promote', { method: 'POST', body: JSON.stringify({ id }) });
export const getPacks = () => api<{ packs: Pack[] }>('/api/packs/list');
export const openPack = (id: number) => api<PackOpeningResult>('/api/packs/open', { method: 'POST', body: JSON.stringify({ id }) });
export const getStadiumInfo = () => api<StadiumInfo>('/api/stadium/info');
export const upgradeStadium = () => api<{ status: string; level: number; cost: number; capacity: number }>('/api/stadium/upgrade', { method: 'POST' });


export type TransferOffer = {
  id: number; amount: number; status: string; created_at: string; player_id: number;
  player_name: string; position: string; age: number; buyer_club_name: string;
  seller_club_name?: string | null; is_incoming: boolean;
};
export type TransferHistoryItem = {
  id: number; player_id: number; player_name: string; price: number;
  created_at: string; from_club_name?: string | null; to_club_name?: string | null;
};
export type ScoutReport = {
  player: Pick<MarketPlayer, 'id' | 'first_name' | 'last_name' | 'position' | 'age' | 'market_value' | 'potential' | 'overall' | 'nationality' | 'rarity'>;
  strengths: { attribute: string; value: number }[];
  weaknesses: { attribute: string; value: number }[];
  potential_gap: number;
  recommendation: string;
};
export const getTransferOffers = () => api<{ offers: TransferOffer[] }>('/api/transfers/offers');
export const getTransferHistory = () => api<{ transfers: TransferHistoryItem[] }>('/api/transfers/history');
export const getScoutReport = (playerId: number) => api<ScoutReport>(`/api/scout/report/${playerId}`);

export const acceptTransferOffer = (offerId: number) => api<{ status: string; offer_id: number; player_id: number; amount: number }>('/api/transfers/offers/accept', { method: 'POST', body: JSON.stringify({ offer_id: offerId }) });
export const rejectTransferOffer = (offerId: number) => api<{ status: string; offer_id: number }>('/api/transfers/offers/reject', { method: 'POST', body: JSON.stringify({ offer_id: offerId }) });

export const getStartingLineup = () => api<StartingLineup>('/api/lineup');
export const autoSelectStartingLineup = () => api<LineupSaveResponse>('/api/lineup/autoselect', { method: 'POST' });
export const saveStartingLineup = (player_ids: number[], formation?: string, bench_ids: number[] = []) => api<LineupSaveResponse>('/api/lineup', { method: 'POST', body: JSON.stringify({ player_ids, formation, bench_ids }) });
export const updateManagerProfile = (manager_name: string) => api<ManagerProfileResponse>('/api/manager/profile', { method: 'POST', body: JSON.stringify({ manager_name }) });
export const updateClubName = (name: string) => api<ClubNameResponse>('/api/club/name', { method: 'POST', body: JSON.stringify({ name }) });
export const updateManagerAvatar = (avatar_id: number) => api<ManagerAvatarResponse>('/api/manager/avatar', { method: 'POST', body: JSON.stringify({ avatar_id }) });
export const updateClubEmblem = (data: { emblem_shape: string; emblem_color: string; emblem_symbol: string }) => api<EmblemUpdateResponse>('/api/club/emblem', { method: 'POST', body: JSON.stringify(data) });

export type LineupRecommendation = { formation: string; formation_slots: FormationSlot[]; starting_ids: number[]; reason: string; source: string };
export const getLineupRecommendation = () => api<LineupRecommendation>('/api/lineup/recommendation');

export const playFriendlyMatch = (mode: 'friendly'|'test' = 'friendly') => api<PlayedFriendlyMatch>(mode === 'test' ? '/api/matches/test' : '/api/matches/friendly', { method: 'POST', body: JSON.stringify({ mode }) });


export type SellPlayerResponse = { status: string; player_id: number; sale_price: number; budget: number };
export const sellPlayer = (player_id: number, price: number) => api<SellPlayerResponse>('/api/transfers/sell', {
  method: 'POST', body: JSON.stringify({ player_id, price })
});
