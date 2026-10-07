import AttentionPanel from "../components/dashboard/AttentionPanel.jsx";
import ExposureCard from "../components/dashboard/ExposureCard.jsx";
import WorkloadTable from "../components/dashboard/WorkloadTable.jsx";
import AgeingChart from "../components/dashboard/AgeingChart.jsx";
import OldestCasesList from "../components/dashboard/OldestCasesList.jsx";

export default function ManagerHome({ data }) {
  return (
    <div className="stack">
      <AttentionPanel attention={data.attention} />
      <WorkloadTable workload={data.workload} />
      <div className="grid-2">
        <AgeingChart ageing={data.ageing} />
        <ExposureCard exposure={data.exposure} />
      </div>
      <OldestCasesList rows={data.oldest} />
    </div>
  );
}
