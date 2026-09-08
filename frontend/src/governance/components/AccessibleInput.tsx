import { Input, type InputProps, type InputRef } from "tdesign-react";
import { useEffect, useRef } from "react";

interface Props extends InputProps {
  ariaInvalid?: boolean;
  ariaDescribedBy?: string;
}
export default function AccessibleInput({ ariaInvalid, ariaDescribedBy, ...props }: Props) {
  const ref = useRef<InputRef>(null);
  useEffect(() => {
    const input = ref.current?.inputElement;
    if (!input) return;
    if (ariaInvalid) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
    if (ariaDescribedBy) input.setAttribute("aria-describedby", ariaDescribedBy); else input.removeAttribute("aria-describedby");
  }, [ariaDescribedBy, ariaInvalid]);
  return <Input ref={ref} {...props} />;
}
