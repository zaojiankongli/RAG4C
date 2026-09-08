import { Button } from "tdesign-react";
import { RefreshIcon } from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type { EnterpriseResourceError } from "../model/enterpriseAdminModel";

interface EnterpriseResourceErrorStateProps {
  error: EnterpriseResourceError;
  onRetry: () => void;
  compact?: boolean;
}

export default function EnterpriseResourceErrorState({
  error,
  onRetry,
  compact = true,
}: EnterpriseResourceErrorStateProps) {
  return (
    <PageState
      status="error"
      compact={compact}
      title={error.title}
      description={error.description}
      extra={
        error.canRetry ? (
          <Button variant="outline" icon={<RefreshIcon />} onClick={onRetry}>
            重新读取
          </Button>
        ) : undefined
      }
    />
  );
}
