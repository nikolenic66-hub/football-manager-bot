import React from 'react';

/** Rotating football shown while the club data is loading. */
export default function LoadingAnimation() {
  return (
    <section
      className="loading-animation"
      role="status"
      aria-live="polite"
      aria-label="Загружаем клуб"
    >
      <div className="loading-animation__ball-wrap" aria-hidden="true">
        <img
          src="/football.png"
          className="loading-animation__ball"
          alt="Загрузка"
        />
      </div>
      <span className="loading-animation__shadow" aria-hidden="true" />
      <p className="loading-animation__label">Загружаем клуб…</p>
      <span className="loading-animation__subtext">Готовим поле к матчу</span>
    </section>
  );
}
