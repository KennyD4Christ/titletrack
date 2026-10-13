import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { createSale, searchBuyers } from "../../api/sales.js";
import Modal from "../common/Modal.jsx";
import { plural } from "../../utils/format.js";
import { toAmount, todayIso } from "../../utils/sales.js";

const NEW = "__new__";

/** Add a sale. Everything is checked again by the server, which refuses an occupied plot by name. */
export default function AddSaleModal({ estates = [], onClose, onCreated }) {
  const [estateChoice, setEstateChoice] = useState("");
  const [estateName, setEstateName] = useState("");
  const [plot, setPlot] = useState("");
  const [buyerMode, setBuyerMode] = useState("new");
  const [buyer, setBuyer] = useState(null);
  const [term, setTerm] = useState("");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState({
    buyer_name: "", phone: "", email: "", price: "", amount_paid: "", sale_date: todayIso(), paid_on: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }));

  useEffect(() => {
    const timer = setTimeout(() => setSearch(term.trim()), 300);
    return () => clearTimeout(timer);
  }, [term]);
  const found = useQuery({
    queryKey: ["buyer-search", search],
    queryFn: () => searchBuyers(search),
    enabled: buyerMode === "existing" && !buyer && search.length >= 2,
  });

  const estateOk = estateChoice === NEW ? estateName.trim() !== "" : estateChoice !== "";
  const buyerOk = buyerMode === "existing" ? Boolean(buyer) : form.buyer_name.trim() !== "";
  const valid = estateOk && buyerOk && plot.trim() !== "" && form.price.trim() !== "" && form.sale_date !== "";

  const payload = { plot_number: plot.trim(), price: form.price.trim(), sale_date: form.sale_date };
  if (estateChoice === NEW) payload.estate_name = estateName.trim();
  else if (estateChoice) payload.estate = Number(estateChoice);
  if (buyerMode === "existing") {
    if (buyer) payload.buyer = buyer.id;
  } else {
    payload.buyer_name = form.buyer_name.trim();
    if (form.phone.trim()) payload.phone = form.phone.trim();
    if (form.email.trim()) payload.email = form.email.trim();
  }
  if (form.amount_paid.trim()) {
    payload.amount_paid = form.amount_paid.trim();
    if (form.paid_on) payload.paid_on = form.paid_on;
  }

  const paidInFull = !Number.isNaN(toAmount(form.amount_paid)) && toAmount(form.amount_paid) > 0
    && toAmount(form.amount_paid) === toAmount(form.price);
  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (key) => (fieldErrors[key] ? <span className="field-error" role="alert">{fieldErrors[key][0]}</span> : null);

  async function submit(event) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      const sale = await createSale(payload);
      await onCreated(sale);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Add a sale" onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        {error && !Object.keys(fieldErrors).length ? (
          <div className="form-error" role="alert">
            <p>{error.message}</p>
            {error.data?.existing_sale ? <p><Link to={`/sales/${error.data.existing_sale.id}`}>View that sale</Link></p> : null}
          </div>
        ) : null}
        <div className="form-grid">
          <label>
            Estate
            <select value={estateChoice} onChange={(e) => setEstateChoice(e.target.value)}>
              <option value="">Choose an estate…</option>
              {estates.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
              <option value={NEW}>New estate…</option>
            </select>
            {fieldError("estate")}
          </label>
          {estateChoice === NEW ? (
            <label>
              New estate name
              <input value={estateName} maxLength={200} onChange={(e) => setEstateName(e.target.value)} />
            </label>
          ) : null}
          <label>
            Plot number
            <input value={plot} maxLength={50} onChange={(e) => setPlot(e.target.value)} />
            {fieldError("plot_number")}
          </label>

          <fieldset className="map-group wide">
            <legend>Buyer</legend>
            <div className="inline">
              <label className="check">
                <input type="radio" name="buyer-mode" checked={buyerMode === "new"} onChange={() => setBuyerMode("new")} />
                New buyer
              </label>
              <label className="check">
                <input type="radio" name="buyer-mode" checked={buyerMode === "existing"} onChange={() => setBuyerMode("existing")} />
                Existing buyer
              </label>
            </div>
            {buyerMode === "new" ? (
              <div className="form-grid">
                <label>
                  Buyer name
                  <input value={form.buyer_name} maxLength={200} onChange={set("buyer_name")} />
                </label>
                <label>
                  Phone (optional)
                  <input value={form.phone} maxLength={40} onChange={set("phone")} />
                </label>
                <label>
                  Email (optional)
                  <input type="email" value={form.email} onChange={set("email")} />
                </label>
              </div>
            ) : buyer ? (
              <p>
                {`Chosen buyer: ${buyer.full_name}${buyer.phone ? ` · ${buyer.phone}` : ""}`}{" "}
                <button type="button" className="btn btn-quiet" onClick={() => setBuyer(null)}>Change buyer</button>
              </p>
            ) : (
              <>
                <label>
                  Find a buyer by name, phone or email
                  <input type="search" value={term} onChange={(e) => setTerm(e.target.value)} />
                </label>
                {search.length >= 2 && found.isSuccess ? (
                  found.data.results.length ? (
                    <ul className="chips">
                      {found.data.results.map((b) => (
                        <li key={b.id}>
                          <button type="button" className="btn" onClick={() => setBuyer(b)}>
                            {`${b.full_name} · ${b.phone || "no phone"} · ${plural(b.sales_count, "sale")}`}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="muted small">No buyer matches. Choose "New buyer" instead.</p>
                ) : null}
                {fieldError("buyer")}
              </>
            )}
          </fieldset>

          <label>
            Price
            <input inputMode="decimal" value={form.price} onChange={set("price")} placeholder="e.g. 25000000" />
            {fieldError("price")}
          </label>
          <label>
            Amount paid so far (optional)
            <input inputMode="decimal" value={form.amount_paid} onChange={set("amount_paid")} />
            {fieldError("amount_paid")}
          </label>
          <label>
            Sale date
            <input type="date" value={form.sale_date} onChange={set("sale_date")} />
            {fieldError("sale_date")}
          </label>
          {form.amount_paid.trim() ? (
            <label>
              Payment date (optional)
              <input type="date" value={form.paid_on} onChange={set("paid_on")} />
              {fieldError("paid_on")}
            </label>
          ) : null}
        </div>
        <ul className="hints">
          {paidInFull ? <li>Paid in full: the documents due on full payment are created straight away.</li> : null}
        </ul>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!valid || busy}>{busy ? "Adding…" : "Add sale"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
