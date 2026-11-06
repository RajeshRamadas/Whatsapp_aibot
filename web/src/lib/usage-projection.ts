const day = 86_400_000;

export function usageProjection(month: string, asOf: string, used: number, limit: number) {
  const start = new Date(month + 'T00:00:00Z');
  const end = Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1);
  const elapsed = Date.parse(asOf) - start.getTime();
  if (!Number.isFinite(end) || elapsed < 0 || elapsed >= end - start.getTime() || !(used > 0 && used < limit)) {
    return null;
  }
  const projected = start.getTime() + Math.max(day, elapsed) * limit / used;
  return Number.isFinite(projected) && projected < end ? new Date(projected) : null;
}
