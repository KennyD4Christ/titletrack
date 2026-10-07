import { obligationsLink } from "../../utils/links.js";
import BarList from "./BarList.jsx";

export default function AgeingChart({ ageing }) {
  const items = [
    { key: "d0_30", label: "0–30 days", value: ageing.d0_30, href: obligationsLink({ age_max: 30 }) },
    { key: "d31_60", label: "31–60 days", value: ageing.d31_60, href: obligationsLink({ age_min: 31, age_max: 60 }) },
    { key: "d61_90", label: "61–90 days", value: ageing.d61_90, href: obligationsLink({ age_min: 61, age_max: 90 }), tone: "warn" },
    { key: "d90_plus", label: "Over 90 days", value: ageing.d90_plus, href: obligationsLink({ age_min: 91 }), tone: "danger" },
  ];
  return (
    <section className="card" aria-labelledby="ageing-title">
      <h2 id="ageing-title">How long documents have been outstanding</h2>
      <BarList items={items} label="Ageing" />
    </section>
  );
}
