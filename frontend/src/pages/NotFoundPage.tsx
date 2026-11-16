import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Card, EmptyState, PageHeader } from "../components/common/ui";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" />
      <Card as="div">
        <EmptyState title="This page does not exist" icon={<Compass className="h-5 w-5" />}
                    action={<Link to="/" className="rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-white hover:bg-primary-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">Go to Home</Link>}>
          Check the address, or use the navigation.
        </EmptyState>
      </Card>
    </>
  );
}
