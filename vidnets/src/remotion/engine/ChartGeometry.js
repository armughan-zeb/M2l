/** Shared signed domain: zero is a baseline, never a fictitious positive bar. */
export function signedChartDomain(values) {
  const numbers = values.map(Number).filter(Number.isFinite);
  const min = Math.min(0, ...numbers);
  const max = Math.max(0, ...numbers);
  const span = max - min || 1;
  return { min, max, zero: -min / span, position: (value) => (Number(value) - min) / span };
}
