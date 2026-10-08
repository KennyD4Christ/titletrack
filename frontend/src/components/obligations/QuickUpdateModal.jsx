import Modal from "../common/Modal.jsx";
import ObligationForm from "./ObligationForm.jsx";

export default function QuickUpdateModal({ obligation, users, onClose, onSaved }) {
  return (
    <Modal title={`Update: ${obligation.type_name} for ${obligation.buyer_name}`} onClose={onClose}>
      <p className="muted small">{obligation.estate_name} · {obligation.plot_number}</p>
      <ObligationForm obligation={obligation} users={users} compact onCancel={onClose} onSaved={onSaved} />
    </Modal>
  );
}
