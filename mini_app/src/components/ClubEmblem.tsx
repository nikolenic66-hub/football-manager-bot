import React from 'react';
const colors: Record<string, string> = { red:'#ef4444', blue:'#3b82f6', green:'#22c55e', yellow:'#facc15', purple:'#a855f7', orange:'#f97316', black:'#171717', white:'#f8fafc' };
const symbols: Record<string, string> = { ball:'⚽', lion:'🦁', eagle:'🦅', wolf:'🐺', lightning:'⚡', crown:'👑', sword:'⚽', star:'⭐' };
export default function ClubEmblem({ shape='shield', color='blue', symbol='ball', size=56 }: { shape?: string; color?: string; symbol?: string; size?: number }) {
  const fill = colors[color] ?? colors.blue;
  return <span className="club-emblem" style={{ width:size, height:size }}>
    <svg viewBox="0 0 64 72" width={size} height={size} aria-label="Эмблема клуба" role="img">
      {shape === 'shield' && <path d="M8 8 L56 8 L53 39 Q48 56 32 65 Q16 56 11 39 Z" />}
      {shape === 'circle' && <circle cx="32" cy="36" r="27" />}
      {shape === 'diamond' && <path d="M32 5 L59 36 L32 67 L5 36 Z" />}
      {shape === 'triangle' && <path d="M32 5 L60 63 L4 63 Z" />}
      {shape === 'star' && <polygon points="32,4 40,25 62,26 45,41 51,63 32,50 13,63 19,41 2,26 24,25" />}
      {shape === 'square' && <rect x="6" y="10" width="52" height="52" rx="5" />}
    </svg><span className="club-emblem-symbol">{symbols[symbol] ?? symbols.ball}</span>
    <style>{`.club-emblem svg{display:block}.club-emblem svg path,.club-emblem svg circle,.club-emblem svg polygon,.club-emblem svg rect{fill:${fill};stroke:rgba(255,255,255,.9);stroke-width:2}.club-emblem{display:inline-flex;align-items:center;justify-content:center;position:relative;flex-shrink:0}.club-emblem-symbol{position:absolute;font-size:${Math.round(size*.34)}px;line-height:1;filter:drop-shadow(0 1px 1px #0006)}`}</style>
  </span>;
}
