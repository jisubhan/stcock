"use client";

import { useId } from "react";

type MiniSparklineProps = {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
  fill?: boolean;
};

export default function MiniSparkline({
  values,
  color = "#ed5b63",
  width = 94,
  height = 34,
  fill = true,
}: MiniSparklineProps) {
  const gradientId = useId().replace(/:/g, "");
  const points = values.filter(Number.isFinite);
  if (points.length < 2) return null;

  const padding = 3;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const coords = points.map((value, index) => ({
    x: padding + (index / (points.length - 1)) * (width - padding * 2),
    y: max === min ? height / 2 : padding + (1 - (value - min) / range) * (height - padding * 2),
  }));
  const line = coords.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
  const area = `${line} L${coords.at(-1)!.x},${height} L${coords[0].x},${height} Z`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" style={{ display: "block", overflow: "visible" }}>
      {fill && (
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.15" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
      )}
      {fill && <path d={area} fill={`url(#${gradientId})`} />}
      <path d={line} fill="none" stroke={color} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export { MiniSparkline };
