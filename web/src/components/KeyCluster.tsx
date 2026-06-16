import type { ChannelBinding } from "../config";

interface Props {
  binding: ChannelBinding;
  heldKeys: Set<string>;
  onPress: (code: string) => void;
  onRelease: (code: string) => void;
}

function keyImage(name: string): string {
  return new URL(`../assets/keys/${name}`, import.meta.url).href;
}

/** T-shaped keycap cluster (one top key over three bottom keys) with the
 * robot's name underneath. Held keys swap to the pack's pressed frame; caps
 * are also pressable with mouse/touch so phones can drive. */
export default function KeyCluster({ binding, heldKeys, onPress, onRelease }: Props) {
  const [top, left, middle, right] = binding.caps;
  return (
    <div className="key-cluster">
      <div className="key-grid">
        <Cap cap={top} held={heldKeys.has(top.code)} style={{ gridArea: "1 / 2" }} onPress={onPress} onRelease={onRelease} />
        <Cap cap={left} held={heldKeys.has(left.code)} style={{ gridArea: "2 / 1" }} onPress={onPress} onRelease={onRelease} />
        <Cap cap={middle} held={heldKeys.has(middle.code)} style={{ gridArea: "2 / 2" }} onPress={onPress} onRelease={onRelease} />
        <Cap cap={right} held={heldKeys.has(right.code)} style={{ gridArea: "2 / 3" }} onPress={onPress} onRelease={onRelease} />
      </div>
      <span className="key-label">{binding.label}</span>
    </div>
  );
}

function Cap({
  cap,
  held,
  style,
  onPress,
  onRelease,
}: {
  cap: { code: string; img: string };
  held: boolean;
  style: React.CSSProperties;
  onPress: (code: string) => void;
  onRelease: (code: string) => void;
}) {
  const src = keyImage(held ? cap.img.replace(/\.png$/, ".pressed.png") : cap.img);
  return (
    <img
      src={src}
      alt={cap.code}
      draggable={false}
      className="keycap"
      style={style}
      // Pointer events cover mouse, touch and pen uniformly. Capture the
      // pointer so the release still fires if the finger slides off the cap.
      onPointerDown={(e) => {
        e.preventDefault();
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        onPress(cap.code);
      }}
      onPointerUp={() => onRelease(cap.code)}
      onPointerCancel={() => onRelease(cap.code)}
    />
  );
}
