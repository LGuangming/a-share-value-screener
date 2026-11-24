"use client";

import { useLayoutEffect, useRef, useState } from "react";

/**
 * Uses the real rendered chip widths to display as many complete reasons
 * as fit on two compact lines. The remaining count includes every hidden item.
 * Widths are rechecked when the table column resizes.
 */
export default function ReviewReasons({
  reasons,
}: {
  reasons: ReadonlyArray<{ code: string; label: string; severity?: "HIGH"|"MEDIUM"|"LOW" }>;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const rulerRef = useRef<HTMLDivElement>(null);
  const [visibleCount, setVisibleCount] = useState(Math.min(reasons.length, 2));

  useLayoutEffect(() => {
    const host = hostRef.current;
    const ruler = rulerRef.current;
    if (!host || !ruler) return;

    const update = () => {
      const width = host.clientWidth;
      if (width <= 0) return;
      const chips = Array.from(
        ruler.querySelectorAll<HTMLElement>(".review-reason")
      );
      const extra = ruler.querySelector<HTMLElement>(".review-more");
      if (!extra) return;
      const chipWidths = chips.map(chip => Math.ceil(chip.getBoundingClientRect().width));
      const gap = 4;

      const fits = (count: number): boolean => {
        let line = 1;
        let used = 0;
        const sizes = chipWidths.slice(0, count);
        if (count < reasons.length) sizes.push(Math.ceil(extra.getBoundingClientRect().width));
        for (const chipWidth of sizes) {
          // A reason longer than one column cannot be fully shown as a chip.
          // It is still available in the tooltip and the stock-detail page.
          const w = Math.min(chipWidth, width);
          if (used > 0 && used + gap + w > width) {
            line++;
            used = 0;
          }
          if (line > 2) return false;
          used += used > 0 ? gap + w : w;
        }
        return true;
      };

      let next = reasons.length;
      while (next > 0 && !fits(next)) next--;
      setVisibleCount(previous => previous === next ? previous : next);
    };

    const observer = new ResizeObserver(update);
    observer.observe(host);
    update();
    return () => observer.disconnect();
  }, [reasons]);

  const hiddenCount = reasons.length - visibleCount;
  return <div ref={hostRef} className="review-reasons" aria-label="主要复核原因"
    title={reasons.map(reason => reason.label).join("；") || "当前规则未识别到重点风险"}>
    {reasons.length
      ? reasons.slice(0, visibleCount).map(reason =>
          <span key={reason.code} className={`review-reason${reason.severity?` review-level-${reason.severity.toLowerCase()}`:""}`}>{reason.label}</span>)
      : <span className="review-muted">当前规则未识别到重点风险</span>}
    {hiddenCount > 0 && <span className="review-more">另有{hiddenCount}项</span>}
    <div className="review-reasons-ruler" ref={rulerRef} aria-hidden="true">
      {reasons.map(reason => <span key={reason.code} className="review-reason">{reason.label}</span>)}
      <span className="review-more">另有{reasons.length}项</span>
    </div>
  </div>;
}
