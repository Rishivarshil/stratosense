/**
 * Magnus formula dewpoint calculation.
 * Returns dewpoint temperature in °C given air temperature and relative humidity.
 */
export function calcDewpoint(temp, humidity) {
  if (temp == null || !humidity || humidity <= 0) return null;
  const a = 17.67;
  const b = 243.5;
  const alpha = (a * temp) / (b + temp) + Math.log(humidity / 100);
  return (b * alpha) / (a - alpha);
}
