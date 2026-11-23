/**
 * Navigation-only fallback.
 *
 * Never swap in a second dashboard heading while switching local screener
 * tabs or moving between pages. Keep the persistent sidebar/header in place
 * and show only a subtle progress bar during a genuine server navigation.
 */
export default function Loading() {
  return <div className="route-loading-indicator" role="status" aria-live="polite"
    aria-label="页面加载中">
    <span className="route-loading-indicator-bar" aria-hidden="true"/>
    <span className="sr-only">页面加载中</span>
  </div>;
}
