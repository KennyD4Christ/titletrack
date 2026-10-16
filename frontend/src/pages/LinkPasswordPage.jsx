import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useEffect } from "react";
import { resetPassword, setPassword } from "../api/passwords.js";
import { errorMessage } from "../utils/staff.js";

const COPY = {
  setup: { title: "Set your password", intro: "Choose a password to finish setting up your account.", call: setPassword },
  reset: { title: "Reset your password", intro: "Choose a new password for your account.", call: resetPassword },
};
const INVALID = "This link is invalid or has expired. Ask your administrator for a new one.";

/** The token arrives in the URL fragment (#token=...), which browsers never send to a server. It is read once into
 *  memory, removed from the address bar, sent only in the request body, and never saved anywhere. */
function readToken(hash) {
  return new URLSearchParams(String(hash || "").replace(/^#/, "")).get("token") || "";
}

export default function LinkPasswordPage({ mode }) {
  const copy = COPY[mode];
  const location = useLocation();
  const navigate = useNavigate();
  const [token, setToken] = useState(() => readToken(location.hash));
  const [password, setPasswordText] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState([]);
  const [state, setState] = useState(token ? "form" : "invalid");     // form | done | invalid

  useEffect(() => {
    if (location.hash) navigate(location.pathname, { replace: true });   // take the secret out of the address bar
  }, [location.hash, location.pathname, navigate]);

  const mismatch = confirm !== "" && password !== confirm;

  async function submit(event) {
    event.preventDefault();
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError("");
    setFieldErrors([]);
    try {
      await copy.call(token, password);
      setToken("");
      setPasswordText("");
      setConfirm("");
      setState("done");
    } catch (err) {
      if (err.status === 400 && err.fieldErrors.password) {
        setFieldErrors(err.fieldErrors.password);          // a weak password: the link is still good, try again
      } else if (err.status === 400) {
        setToken("");
        setState("invalid");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  let body;
  if (state === "done") {
    body = (
      <>
        <p role="status">Your password is set. You can now sign in.</p>
        <Link className="btn btn-primary" to="/login">Go to sign in</Link>
      </>
    );
  } else if (state === "invalid") {
    body = (
      <>
        <p className="form-error" role="alert">{INVALID}</p>
        <Link to="/login">Go to sign in</Link>
      </>
    );
  } else {
    body = (
      <form className="edit-form" onSubmit={submit}>
        <p className="muted">{copy.intro}</p>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {fieldErrors.length ? <ul className="form-error" role="alert">{fieldErrors.map((m) => <li key={m}>{m}</li>)}</ul> : null}
        <label>
          New password
          <input type="password" value={password} onChange={(e) => setPasswordText(e.target.value)} autoComplete="new-password" autoFocus required />
        </label>
        <label>
          Confirm new password
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
        </label>
        {mismatch ? <span className="field-error" role="alert">The two passwords do not match.</span> : null}
        <button type="submit" className="btn btn-primary" disabled={busy || !password || mismatch}>
          {busy ? "Saving…" : "Save password"}
        </button>
      </form>
    );
  }

  return (
    <div className="login">
      <div className="card login-card">
        <h1>{copy.title}</h1>
        {body}
      </div>
    </div>
  );
}
