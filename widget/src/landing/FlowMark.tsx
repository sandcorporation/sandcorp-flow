/** Flow 제품 아이콘(sandcorp-design v0.4.0). 위젯 base(/embed/) 아래 public/flow/ — 루트 / 랜딩에서도 /embed/flow/ 로 맞는다. 장식이라 alt 는 비운다. */
export default function FlowMark({ size = 20 }: { size?: number }) {
  return <img className="flow-mark" src={`${import.meta.env.BASE_URL}flow/icon.svg`} alt="" aria-hidden width={size} height={size} />
}
