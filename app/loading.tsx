export default function Loading() {
  return <main className="page-container" role="status" aria-live="polite" aria-label="正在加载页面">
    <div className="page-top">
      <div><div className="eyebrow">A-SHARE RESEARCH WORKSPACE</div>
        <h1>正在加载研究数据</h1>
        <p className="page-lead">正在读取最近一次已发布的筛选结果。</p>
      </div>
    </div>
    <div className="panel"><div className="page-loading-bar" aria-hidden="true"/>
      <div className="page-loading-line" aria-hidden="true"/>
      <div className="page-loading-line page-loading-short" aria-hidden="true"/>
    </div>
    <span className="sr-only">正在加载，请使用已显示的导航继续浏览。</span>
  </main>;
}
