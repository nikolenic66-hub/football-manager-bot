import React, { useCallback, useEffect, useState } from 'react';
import { getMe, getClub, TELEGRAM_REQUIRED_MESSAGE, type Club, type Me } from './api';
import CreateClubScreen from './screens/CreateClubScreen';
import ClubScreen from './ClubScreen';
import LoadingAnimation from './components/LoadingAnimation';
import ErrorBoundary from './components/ErrorBoundary';

type State = 'loading' | 'create' | 'club' | 'error' | 'telegram-required';

function AppContent() {
  const [state, setState] = useState<State>('loading');
  const [club, setClub] = useState<Club | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(async () => {
    setState('loading');
    setError('');
    try {
      const currentUser = await getMe();
      setMe(currentUser);
      if (currentUser.club_id == null) {
        setClub(null);
        setState('create');
        return;
      }
      // Verify the club endpoint returns 200 before showing the game screen.
      const loadedClub = await getClub(currentUser.club_id);
      setClub(loadedClub);
      setState('club');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Не удалось загрузить клуб';
      if (message === TELEGRAM_REQUIRED_MESSAGE) {
        setClub(null);
        setState('telegram-required');
      } else if (/404|Club not found|create a club first|клуб не найден/i.test(message)) {
        setClub(null);
        setState('create');
      } else {
        setError(message);
        setState('error');
      }
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  if (state === 'loading') return <div className="app-shell"><LoadingAnimation /></div>;
  if (state === 'telegram-required') return <div className="app-shell telegram-required-screen"><section className="card"><h1>Откройте приложение из Telegram</h1><p>Для входа в игру запустите Mini App через кнопку в Telegram-боте.</p></section></div>;
  if (state === 'create') return <CreateClubScreen onCreated={() => setRefreshKey(v => v + 1)} />;
  if (state === 'error') return <div className="app-shell"><section className="card"><h2>Не удалось открыть игру</h2><p className="api-error">{error}</p><button className="btn" onClick={() => setRefreshKey(v => v + 1)}>Повторить</button></section></div>;
  return <ClubScreen key={club?.id ?? 'club'} club={club!} managerName={me?.first_name || 'Менеджер'} managerAvatarId={me?.avatar_id ?? 1} />;
}


export default function App() {
  return (
    <ErrorBoundary>
      <AppContent />
    </ErrorBoundary>
  );
}
