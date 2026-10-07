import { formatNaira, formatNairaCompact } from "../../utils/format.js";

/** Compact by default (₦5.53bn); the exact figure is in the tooltip. */
export default function Money({ value, full = false }) {
  return <span title={formatNaira(value)}>{full ? formatNaira(value) : formatNairaCompact(value)}</span>;
}
