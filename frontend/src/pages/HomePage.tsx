import { Rocket } from "lucide-react";
import { Card, EmptyState, PageHeader } from "../components/common/ui";

/** Empty on purpose: we fill this in once the problem statement is picked (see docs/PLAN.md). */
export function HomePage() {
  return (
    <>
      <PageHeader title="Home" description="Our hackathon app. The problem statement is picked on the day." />
      <Card as="div">
        <EmptyState title="Nothing here yet" icon={<Rocket className="h-5 w-5" />}>
          This page is filled in once the problem is known. System Health and Audit History already work.
        </EmptyState>
      </Card>
    </>
  );
}
