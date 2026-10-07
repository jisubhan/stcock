export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers }, cache: 'no-store' });
  if (!response.ok) {
    let message = `요청 실패 (${response.status})`;
    try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data); } catch { /* use status */ }
    throw new Error(message);
  }
  return response.json();
}
export const money = (n: number) => new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(n);
export const pct = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
export const compactMoney = (n: number) => n >= 1e12 ? `${(n / 1e12).toFixed(2)}조` : n >= 1e8 ? `${Math.round(n / 1e8).toLocaleString('ko-KR')}억` : `${money(n)}원`;
export const stateLabel: Record<string, string> = { SCANNING: '분석 중', WATCH: '관찰 중', APPROACHING_ENTRY: '진입 구간 접근', ENTRY_READY: '진입 조건 충족', BREAKOUT_CONFIRM: '돌파 확인', TAKE_PROFIT_ZONE: '목표 구간 도달', INVALIDATED: '추천 무효화' };
