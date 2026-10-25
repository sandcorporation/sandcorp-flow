/** Flow 제품 아이콘(sandcorp-design v0.4.0 assets/flow/icon.svg, public/flow/). 장식이라 alt 는 비워 둔다 — 옆 글자가 이름이다. */
export default function FlowMark({ className = 'h-5 w-5' }: { className?: string }) {
  return <img src={`${import.meta.env.BASE_URL}flow/icon.svg`} alt="" aria-hidden className={`flex-none ${className}`} />
}
