"use client";

import { useEffect, useRef, useState } from "react";
import {
  BaselineSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type AutoscaleInfo,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { Minus, Plus, RotateCcw } from "lucide-react";

export type ChartCandle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ma5?: number;
  ma5_5m?: number | null;
  status?: 'CLOSED' | 'PARTIAL';
};

export type ChartLevels = {
  ma5: number;
  pivot: number;
  boxHigh: number;
  boxLow: number;
  stop: number;
  entryLow: number;
  entryHigh: number;
  target1: number;
  target2: number;
};

type PriceChartProps = {
  candles: ChartCandle[];
  levels: ChartLevels;
  symbol: string;
  interval: "1d" | "5m" | "1m";
  height?: number;
  showVolume?: boolean;
  maPeriod?: number;
  significantCandleDate?: string;
};

type HoverData = ChartCandle & { label: string };
const formatNumber = (value: number) => new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 }).format(value);

function utcTime(value: string): UTCTimestamp | null {
  if (/^\d+(?:\.\d+)?$/.test(value)) {
    const numeric = Number(value);
    return Math.floor(numeric > 1e12 ? numeric / 1000 : numeric) as UTCTimestamp;
  }
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? `${value}T00:00:00Z`
    : /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) as UTCTimestamp : null;
}

export default function PriceChart({ candles, levels, symbol, interval, height = 300, showVolume = true, maPeriod = 5, significantCandleDate }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [hover, setHover] = useState<HoverData | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    setHover(null);

    // Normalizing to UTC also gives intraday and daily data the same stable type.
    const byTime = new Map<UTCTimestamp, ChartCandle>();
    for (const candle of candles) {
      const time = utcTime(candle.time);
      if (time !== null && [candle.open, candle.high, candle.low, candle.close, candle.volume].every(Number.isFinite)) {
        byTime.set(time, candle);
      }
    }
    const data = [...byTime].sort(([a], [b]) => a - b).map(([time, candle]) => ({ ...candle, time }));

    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 240),
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#96929a",
        fontSize: 10,
        fontFamily: "Inter, Pretendard, -apple-system, BlinkMacSystemFont, sans-serif",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#f3f1f5", style: LineStyle.Dotted },
        horzLines: { color: "#efedf2", style: LineStyle.Dashed },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#aea4c7", style: LineStyle.Dashed, labelBackgroundColor: "#6d568e" },
        horzLine: { color: "#aea4c7", style: LineStyle.Dashed, labelBackgroundColor: "#6d568e" },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.13, bottom: showVolume ? 0.24 : 0.12 },
        minimumWidth: 65,
      },
      timeScale: {
        borderVisible: false,
        timeVisible: interval !== "1d",
        secondsVisible: false,
        rightOffset: 6,
        minBarSpacing: 3,
        ...(interval !== '1d' ? { tickMarkFormatter: (time: unknown) => new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(Number(time) * 1000)) } : {}),
      },
      localization: { locale: "ko-KR", priceFormatter: formatNumber },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
    });
    chartRef.current = chart;

    // A constant baseline series creates a true price-aligned entry-zone fill.
    const entryBand = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: levels.entryLow },
      topFillColor1: "rgba(139, 92, 246, 0.075)",
      topFillColor2: "rgba(139, 92, 246, 0.075)",
      bottomFillColor1: "transparent",
      bottomFillColor2: "transparent",
      topLineColor: "transparent",
      bottomLineColor: "transparent",
      baseLineVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => null,
    });
    entryBand.setData(data.map(({ time }) => ({ time, value: levels.entryHigh })));

    const price = chart.addSeries(CandlestickSeries, {
      upColor: "#ed5b63",
      downColor: "#3b93a5",
      borderVisible: false,
      wickUpColor: "#ed5b63",
      wickDownColor: "#3b93a5",
      priceLineVisible: false,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const info = original();
        if (!info?.priceRange) return info;
        return {
          ...info,
          priceRange: {
            minValue: Math.min(info.priceRange.minValue, levels.stop, levels.entryLow) * 0.998,
            maxValue: Math.max(info.priceRange.maxValue, levels.target2, levels.entryHigh) * 1.002,
          },
        };
      },
    });
    price.setData(data.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
    if (interval === '1d' && significantCandleDate) {
      const time = utcTime(significantCandleDate);
      if (time !== null && byTime.has(time)) createSeriesMarkers(price, [{ time, position: 'aboveBar', color: '#b496d2', shape: 'arrowDown', text: '기준봉' }]);
    }

    const movingAverage = chart.addSeries(LineSeries, {
      color: "#9b78e3",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerRadius: 3,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    movingAverage.setData(data.flatMap<{ time: UTCTimestamp; value?: number }>((candle, index) => {
      if (interval === '5m') {
        // Server-provided, completed contiguous 5m observations only. Whitespace
        // keeps partial/gapped windows from becoming a fabricated recovery line.
        return [{ time: candle.time, ...(candle.status === 'CLOSED' && typeof candle.ma5_5m === 'number' && Number.isFinite(candle.ma5_5m) ? { value: candle.ma5_5m } : {}) }];
      }
      if (interval === '1m') return [{ time: candle.time, value: levels.ma5 }];
      if (index < maPeriod - 1) return [];
      const window = data.slice(Math.max(0, index - maPeriod + 1), index + 1);
      const value = candle.ma5 ?? window.reduce((sum, item) => sum + item.close, 0) / window.length;
      return [{ time: candle.time, value }];
    }));
    if (interval === '5m' && Number.isFinite(levels.ma5)) {
      price.createPriceLine({ price: levels.ma5, title: 'MA5_DAILY', color: '#baa8d4',
        lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false });
    }

    if (showVolume) {
      const volume = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      volume.priceScale().applyOptions({ visible: false, scaleMargins: { top: 0.82, bottom: 0.04 } });
      volume.setData(data.map(({ time, volume, open, close }) => ({
        time,
        value: volume,
        color: close >= open ? "rgba(237, 91, 99, 0.24)" : "rgba(59, 147, 165, 0.24)",
      })));
    }

    const lines = [
      { price: levels.target2, title: "목표 2", color: "#54a291", lineStyle: LineStyle.Dashed },
      { price: levels.target1, title: "목표 1", color: "#7fae97", lineStyle: LineStyle.Dashed },
      { price: levels.pivot, title: "피봇", color: "#b29abf", lineStyle: LineStyle.Dotted },
      { price: levels.entryHigh, title: "진입 상단", color: "#ab90de", lineStyle: LineStyle.Dotted },
      { price: levels.entryLow, title: "진입 하단", color: "#ab90de", lineStyle: LineStyle.Dotted },
      { price: levels.stop, title: "손절", color: "#dd8992", lineStyle: LineStyle.Dashed },
      { price: levels.boxHigh, title: "박스 상단", color: "#c5b899", lineStyle: LineStyle.Dotted },
      { price: levels.boxLow, title: "박스 하단", color: "#c5b899", lineStyle: LineStyle.Dotted },
    ];
    for (const line of lines) {
      if (Number.isFinite(line.price)) price.createPriceLine({ ...line, lineWidth: 1, axisLabelVisible: false, lineVisible: true });
    }

    chart.subscribeCrosshairMove((event) => {
      if (!event.time || !event.point || event.point.x < 0 || event.point.y < 0) {
        setHover(null);
        return;
      }
      const candle = byTime.get(event.time as UTCTimestamp);
      if (!candle) return;
      const date = new Date(Number(event.time) * 1000);
      const label = new Intl.DateTimeFormat("ko-KR", {
        timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
        ...(interval !== "1d" ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
      }).format(date);
      setHover({ ...candle, label });
    });
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(([entry]) => {
      if (entry && entry.contentRect.width > 0) chart.applyOptions({ width: Math.round(entry.contentRect.width) });
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      chartRef.current = null;
      chart.remove();
    };
  }, [candles, levels, interval, height, showVolume, maPeriod, significantCandleDate]);

  const zoom = (factor: number) => {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    const middle = (range.from + range.to) / 2;
    const half = Math.max(4, (range.to - range.from) * factor / 2);
    scale.setVisibleLogicalRange({ from: middle - half, to: middle + half });
  };

  const controlStyle = {
    display: "grid", placeItems: "center", width: 25, height: 25,
    border: "1px solid #eeeaf2", borderRadius: 6, background: "rgba(255,255,255,0.94)",
    color: "#91869e", cursor: "pointer",
  } as const;

  return (
    <div style={{ position: "relative", width: "100%", minWidth: 0 }} role="group" aria-label={`${symbol} ${interval === "1d" ? "일봉" : interval === "5m" ? "5분봉" : "1분봉"} 가격 및 거래량 차트`}>
      <div ref={containerRef} style={{ width: "100%", height }} data-testid="price-chart" />
      <div style={{ position: "absolute", left: 10, right: 70, top: 8, pointerEvents: "none", zIndex: 2, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "5px 12px", fontSize: 10, color: "#948b9e" }}>
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}><i style={{ width: 10, height: 2, background: "#9b78e3" }} />{interval === '5m' ? `MA5_5M${candles.some(c => c.status === 'CLOSED' && typeof c.ma5_5m === 'number' && Number.isFinite(c.ma5_5m)) ? '' : ' · 이력 대기'}` : `MA${maPeriod}${interval === '1m' ? '_DAILY' : ''}`}</span>
        {interval === '5m' && <span style={{ display: "flex", alignItems: "center", gap: 5 }}><i style={{ width: 10, height: 1, background: "#baa8d4" }} />MA5_DAILY</span>}
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}><i style={{ width: 7, height: 7, background: "#eee6fb", borderRadius: 2 }} />진입 구간</span>
        {showVolume && <span style={{ display: "flex", alignItems: "center", gap: 5 }}><i style={{ width: 7, height: 7, background: "#f1d5d8", borderRadius: 2 }} />거래량</span>}
      </div>
      {hover && (
        <div style={{ position: "absolute", top: 30, left: 10, zIndex: 3, pointerEvents: "none", padding: "8px 11px", border: "1px solid #ede8f2", borderRadius: 8, background: "rgba(255,255,255,0.96)", boxShadow: "0 3px 12px #30204308", fontSize: 10, lineHeight: 1.7, color: "#82778e" }}>
          <div style={{ fontWeight: 600, color: "#5b4d6a", marginBottom: 3 }}>{hover.label} · {symbol}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0 10px" }}>
            <span>시 {formatNumber(hover.open)}</span><span>고 {formatNumber(hover.high)}</span><span>저 {formatNumber(hover.low)}</span>
            <span style={{ color: hover.close >= hover.open ? "#d55662" : "#348294" }}>종 {formatNumber(hover.close)}</span>
          </div>
          {showVolume && <div>거래량 {formatNumber(hover.volume)}</div>}
          {hover.status === 'PARTIAL' && <div style={{ color: '#a1836d' }}>미완성 봉 · 전략 확인 근거 제외</div>}
        </div>
      )}
      <div style={{ position: "absolute", left: 10, bottom: 29, display: "flex", gap: 4, zIndex: 3 }}>
        <button type="button" style={controlStyle} aria-label="차트 확대" title="차트 확대" onClick={() => zoom(0.75)}><Plus size={12} /></button>
        <button type="button" style={controlStyle} aria-label="차트 축소" title="차트 축소" onClick={() => zoom(1.3)}><Minus size={12} /></button>
        <button type="button" style={controlStyle} aria-label="차트 초기화" title="전체 기간 보기" onClick={() => chartRef.current?.timeScale().fitContent()}><RotateCcw size={11} /></button>
      </div>
    </div>
  );
}

export { PriceChart };
