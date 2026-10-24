import '@testing-library/jest-dom'

// jsdom은 scrollIntoView를 구현하지 않는다 — 채팅 히스토리의 자동 스크롤 effect용 no-op 폴리필.
if (!window.HTMLElement.prototype.scrollIntoView) {
  window.HTMLElement.prototype.scrollIntoView = () => {}
}

// recharts ResponsiveContainer가 쓰는 ResizeObserver가 jsdom엔 없다 — no-op 폴리필.
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
}

// React Flow(@xyflow/react)가 뷰포트 변환에 쓰는 DOMMatrixReadOnly 가 jsdom 엔 없다 — 행렬 값만 읽는 최소 대역.
if (!('DOMMatrixReadOnly' in globalThis)) {
  globalThis.DOMMatrixReadOnly = class {
    constructor(t) { const m = /matrix\(([^)]+)\)/.exec(t || ''); const v = m ? m[1].split(',').map(Number) : [1, 0, 0, 1, 0, 0]; this.m22 = v[3]; this.m41 = v[4]; this.m42 = v[5]; this.m11 = v[0] }
  }
}
