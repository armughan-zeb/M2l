'use strict';

/** Shared duration rules; mirrors the CMS media validator. */

function durationMinutes(value) {
  const text = String(value == null ? '' : value).trim().toLowerCase();
  const number = Number.parseFloat(text);
  if (!Number.isFinite(number) || number <= 0) return 10;
  if (/sec|^\d+s$/.test(text)) return number / 60;
  if (/hour|hr|^\d+h$/.test(text)) return number * 60;
  return number;
}

function minimumVideoDurationSeconds(expectedMinutes) {
  return Math.max(30, Number(expectedMinutes || 10) * 60);
}

function minimumScriptWords(expectedMinutes) {
  const minutes = Math.max(1, Number(expectedMinutes) || 8);
  const targetWords = Math.round(minutes * 150);
  const allowance = targetWords >= 1000 ? 200 : Math.min(200, Math.round(targetWords * 0.15));
  const baseMinimum = Math.max(80, targetWords >= 1400 ? 1300 : targetWords - allowance);
  const rateMinimum = Math.round(minutes * 135);
  return Math.min(baseMinimum, rateMinimum);
}

module.exports = { durationMinutes, minimumVideoDurationSeconds, minimumScriptWords };
