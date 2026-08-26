import React, { Component, type ReactNode } from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

/** 渲染期异常兜底:任一组件抛错时给出可恢复的降级 UI,而不是整页白屏 */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 48, fontFamily: 'sans-serif' }}>
          <h2>页面渲染出错</h2>
          <pre style={{ whiteSpace: 'pre-wrap', color: '#888', fontSize: 13 }}>
            {this.state.error.message || String(this.state.error)}
          </pre>
          <button onClick={() => window.location.reload()}>刷新页面</button>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
