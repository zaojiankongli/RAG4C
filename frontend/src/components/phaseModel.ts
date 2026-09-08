/** 流式阶段事件 -> 紧凑进度步骤（0=检索 1=生成 2=验证）。 */
export function phaseToStep(phase: string): number {
  switch (phase) {
    case "retrieving":
    case "retrieving_again":
      return 0;
    case "retrieved":
    case "generating":
      return 1;
    case "verifying":
      return 2;
    default:
      return 0;
  }
}
