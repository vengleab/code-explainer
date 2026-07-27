import React from 'react';

/**
 * Shared Visualizer Utilities — Color palettes, heat map calculation, 
 * number formatting, and syntax-highlighted expression rendering.
 */

/**
 * Get theme color palette for canvas rendering.
 * @param {string} theme - 'dark' | 'light'
 * @returns {object} Palette configuration object
 */
export function getC_(theme) {
  const isDark = theme === 'dark';
  return {
    dark: isDark ? '#f5f1ea' : '#1c1917',
    light: isDark ? '#211b16' : '#faf9f5',
    mid: isDark ? '#a39c90' : '#b0aea5',
    lgray: isDark ? 'rgba(255, 255, 255, 0.12)' : '#e8e6dc',
    orange: isDark ? '#f97316' : '#d97757',
    blue: isDark ? '#38bdf8' : '#6a9bcc',
    green: isDark ? '#34d399' : '#788c5d',
    nan: isDark ? '#f87171' : '#c96b5a',
    purple: isDark ? '#a855f7' : '#9333ea',
  };
}

/**
 * Interpolate heat map color (RGB array) for canvas cell rendering based on value range.
 */
export function heat(v, lo = 0, hi = 100, isDark = false) {
  if (typeof v !== 'number' || isNaN(v)) {
    return isDark ? [55, 35, 40] : [247, 235, 232];
  }
  let x = hi <= lo ? 0.5 : Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  const a = isDark ? [30, 58, 138] : [130, 160, 190];
  const b = isDark ? [30, 41, 59] : [240, 236, 225];
  const c = isDark ? [217, 119, 87] : [217, 119, 87];
  const mix = (p, q, t) => [
    Math.round(p[0] + (q[0] - p[0]) * t),
    Math.round(p[1] + (q[1] - p[1]) * t),
    Math.round(p[2] + (q[2] - p[2]) * t),
  ];
  return x < 0.5 ? mix(a, b, x * 2) : mix(b, c, (x - 0.5) * 2);
}

/**
 * Format numeric cell values or return string representation.
 */
export function fmt(v) {
  if (typeof v === 'number') {
    return Number.isInteger(v) ? v : Math.round(v * 10) / 10;
  }
  return String(v);
}

/**
 * Syntax-highlight numbers and operators in mathematical expressions.
 */
export function colorExpr(s) {
  if (!s) return null;
  const parts = s.split(/(-?\d+\.?\d*)|(>=|<=|==|!=|[+\-*/><])/g);
  return parts.map((p, i) => {
    if (!p) return null;
    if (/^-?\d+\.?\d*$/.test(p)) return <span key={i} style={{ color: '#f0b47a' }}>{p}</span>;
    if (/^(>=|<=|==|!=|[+\-*/><])$/.test(p)) return <span key={i} style={{ color: '#8fb9e0' }}>{p}</span>;
    return <span key={i}>{p}</span>;
  });
}
