import { Button, Tag, Typography } from "../ui/index";
import { CloudServerOutlined, ReloadOutlined, RocketOutlined } from "../ui/icons";
import { useConnection } from "../context/ConnectionContext";
import { FONT_SIZE } from "../theme/tokens";

const { Text } = Typography;

export default function ModeBanner() {
  const { online, checking, refresh } = useConnection();
  if (online !== false) return null;
  
  return (
    <div className="mode-banner">
      <Tag color="warning" className="mode-tag">
        <CloudServerOutlined /> 演示模式
      </Tag>
      <Text strong style={{ fontSize: FONT_SIZE.md }}>
        当前正在展示示例内容
      </Text>
      <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
        连接真实服务后，这里会自动显示你的资料和实际回答
      </Text>
      <Button 
        size="small" 
        icon={<ReloadOutlined spin={checking} />} 
        onClick={() => void refresh()}
      >
        检查连接
      </Button>
      <RocketOutlined className="mode-rocket" />
    </div>
  );
}
