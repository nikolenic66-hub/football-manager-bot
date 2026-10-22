# Football Manager Telegram Mini App

Мобильный фронтенд в стиле пользовательских референсов. Основа — React + TypeScript + Vite, по архитектуре официального [Telegram Mini Apps React template](https://github.com/Telegram-Mini-Apps/reactjs-template).

## Что уже есть
- Адаптивная тёмно-синяя тема с зелёными акцентами и нижней навигацией.
- Экраны: главная, команда, состав с фильтрами, тактика с 2D-полем и выбором схемы, live-матч, трансферный рынок, AI-советник (демо-ответ), тренировки, таблица лиги, стадион, академия, скаут-центр и паки.
- Навигация, фильтры, выбор схемы, шкалы тактики, завершение тренировки и действия на карточках работают в демо-режиме.
- Подготовлена как отдельная папка `miniapp`, не меняющая Python-бот.

## Запуск локально
Требуется Node.js 20+.

```bash
cd miniapp
npm install
npm run dev
```

## Production build

```bash
npm run build
```

Готовая сборка появится в `miniapp/dist`.

## Telegram
Для открытия из Telegram нужен опубликованный HTTPS URL. После размещения в HTTPS-хостинге укажите URL Mini App в настройках бота через BotFather. Текущий фронтенд использует демонстрационные данные: перед production необходимо подключить авторизованный API существующего Python backend и валидировать Telegram `initData` на сервере. Не доверяйте `initDataUnsafe` на серверной стороне.

## API integration contract (next step)
Рекомендуемые endpoints для интеграции с существующим backend: `GET /api/me`, `GET /api/club`, `GET /api/squad`, `GET /api/market`, `POST /api/transfers/bids`, `GET /api/matches/live`, `POST /api/tactics`, `POST /api/training/complete`. Эти маршруты перечислены как контракт интеграции и ещё не реализованы этим frontend-пакетом.
