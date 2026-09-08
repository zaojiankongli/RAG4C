import { MessagePlugin } from "tdesign-react";

export const message = {
  success: (content: React.ReactNode) => MessagePlugin.success(content),
  error: (content: React.ReactNode) => MessagePlugin.error(content),
  warning: (content: React.ReactNode) => MessagePlugin.warning(content),
  info: (content: React.ReactNode) => MessagePlugin.info(content),
};
