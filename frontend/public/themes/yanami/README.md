# 八奈见主题素材：实际前端引用审计（SVG＋生产人物 PNG）

审计日期：2026-09-28。来源根目录：`C:/Users/饶策/Desktop/me/WeKnora-0.8.0`。目标目录：`D:/program_project/python_project/RAG4C/frontend/public/themes/yanami`。

**范围是当前本地源码的可达引用链，不是素材文件名、预览目录或历史提交。** 来源 HEAD 为 `6408ab50a26ae2ae158139566e30db47dd610d69`，但主题目录含未跟踪文件，入口、菜单等也有本地修改；该提交号不能复现当前主题，以本页源路径和 SHA256 为准。本次未连接运行中的 WeKnora 页面，不将静态引用核验表述为所有设备／状态下均已实际显示。

## 结论与选材

- 本目录共 **23 个 SVG＋1 个生产人物 PNG**。其中 **17 个 SVG 与 1 个 PNG 有当前前端直接引用证据**；另 **6 个原有 SVG 仅保留、不纳入推荐**。本轮最终新增 12 个 SVG（9 个导航图标、3 个饰品）和 1 个生产 PNG；原有 11 个 SVG 字节不变。
- **欢迎页主视觉使用 `materials/iridescent-character.png`**：这是从实际前端 import 的生产路径复制的原文件，不是从预览目录取图。人物项目实际用 PNG，导航／饰品才使用下文直接引用的 SVG。
- **导航真正使用 `frontend/src/assets/img/` 的普通／选中态 SVG**，由 `getImgSrc()` 解析；八奈见主题通过 `hue-rotate(53deg)` 为选中态调色。不能把 `theme/anna/icons/anna-zhishiku-green.svg` 等同名变体误认为当前导航加载文件。`navigation/` 保留实际加载文件的原始字节，选中态原文件仍为绿色，主题色来自 CSS。
- **品牌／辅助装饰使用** `materials/iridescent-bow.svg`、`materials/iridescent-ribbon.svg`、`materials/clover-sprig.svg`、`materials/sakura-branch.svg`。`clover-corner.svg` 和 `sakura-parasol.svg` 则在来源组织页／登录页／空状态中引用。它们不再替代人物作为欢迎页主视觉。
- **不交付或使用 docs 角色 SVG 母版、不从预览目录取材。** 按用户要求，仅撤销本轮新增且 SHA256 已确认的 `characters/anna-iridescent-character.svg`；原有素材未删除。当前交付不含该文件，也不再建议引用该 URL。
- 原 README 将所有 `icons/*.svg` 统称为对应导航图标的说法不准确，现以以下分级证据取代。本次没有修改任何 RAG4C `src` 文件；已有页面如何接入这些素材由后续 CSS／组件集成决定。

## 欢迎页人物主视觉：生产 PNG（已复制）

**直接引用 URL：`/themes/yanami/materials/iridescent-character.png`。** 选择的是当前欢迎页实际加载的虹彩八奈见半身人物，不是汽水杯／缎带拼贴。

| 目标文件 | 用途／状态 | 当前引用点 | 像素尺寸 | 源文件（绝对路径） | SHA256（源＝目标） |
| --- | --- | --- | --- | --- | --- |
| `materials/iridescent-character.png` | 当前欢迎页生产人物原文件，直接引用 | W:2 import → W:33 img；Q:4、57；R:162、168 | `758 × 1280` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/materials/iridescent-character.png` | `CCD65B7D3D052416D590FF930525E7760EAB5EDFD8EF3AFAA5613F8A2398E136` |

**实际大小：869,076 字节（约 848.71 KiB／0.829 MiB；十进制约 869.08 KB）。** 已核验 PNG 文件签名、IHDR 尺寸以及源／目标 SHA256；未缩放、压缩、转码或包装成 SVG。

引用证据：W:2 为 `import character from '@/assets/theme/anna/materials/iridescent-character.png';`，W:33 为 `<img class="anna-welcome__character" :src="character" ... width="758" height="1280" />`。Q:57 导入 `AnnaWelcome`、Q:4 渲染；R:162、168 将该页面接入路由。**本次文件来自以上生产 import 路径，其历史生成方式不改变当前作为生产人物图片的使用事实。**

```tsx
<img
  className="yanami-welcome-character"
  src="/themes/yanami/materials/iridescent-character.png"
  width={758}
  height={1280}
  alt="八奈见杏菜"
  decoding="async"
/>
```

```css
html[data-theme="anime"] .yanami-welcome-character {
  display: block;
  height: clamp(280px, 38vh, 440px);
  width: auto;
  max-width: 100%;
  object-fit: contain;
  object-position: center bottom;
}
```

以上只提供接入示例，不改动 src。建议保留人物单独构图、按原比例显示；若相邻标题已提供人物名称且图片仅装饰，可用 `alt="" aria-hidden="true"`。

## 入口与引用链

下文 `N:69` 等表示“引用文件索引 N 的第 69 行”，`→` 表示 import 到模板使用。所有行号对应本次审计快照，文件变化后可用索引中的 SHA256 判断是否仍是同一版本。

1. **应用入口**：E:76 加载 `/src/main.ts`；B:12–15 顺序加载四个八奈见 CSS 文件（T、M、C、D）。B:3–4 导入 App 和 router，B:58、63 使用 router 并挂载应用。
2. **导航／侧栏品牌**：R:100 挂载平台页；P:22 导入菜单，P:3 渲染 `<Menu>`。S:27、32–34 定义新对话、知识库、智能体、组织入口；N:457–460 筛选 `topMenuItems`；N:98–106 遍历并渲染图标；N:1099–1108 切换文件名；N:1185–1186 将文件名解析到 `/src/assets/img/`；T:1145–1147 调整选中态色相。智能体／组织入口是否显示仍受来源菜单能力条件控制。
3. **欢迎页**：R:162、168 加载对话创建页；Q:57 导入 `AnnaWelcome`，Q:4 渲染它；W:3–6 导入四个 SVG，W:14–15、19、32 实际绑定图片。W 中存在桌面／移动布局规则，不等于所有装饰在所有宽度都显示。
4. **登录页与装饰覆盖**：R:56 加载登录页；L:25–28 导入 SVG，L:488、550–551、553 绑定到模板。M:100 后加载并覆盖 T:1078 的 `.empty-img`，当前该 CSS 规则指向 `sakura-parasol.svg`；不能仅因旧 `anna-upload.svg` 仍有文本引用就把它当成此处最终插画。
5. **排除死分支**：N:106 虽含 `settingIcon`／`logoutIcon` 三元分支，N:457–460 的实际顶部菜单不含这两项，底部 N:209–211 改用 `<UserMenu />`。因此本次不凭变量名或旧菜单定义复制 `setting*.svg`、`logout.svg`。

## 素材清单与 SHA256

以下“源文件”均为可直接核验的绝对路径；目标文件相对本 README 所在目录。每行 SHA256 同时适用于源文件与目标文件（已逐字节比对）；本节 SVG 的 `viewBox` 保留原值，没有重画、改色或 SVG 优化。人物 PNG 单列于上节，同样逐字节复制。

### 导航：当前菜单实际加载的文件

| 目标文件 | 用途／状态 | 当前引用点 | viewBox | 源文件（绝对路径） | SHA256（源＝目标） |
| --- | --- | --- | --- | --- | --- |
| `navigation/search.svg` | 搜索／命令面板 | N:22、91；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/search.svg` | `72BDDC7D0B1AA74B677EB2C31558EC3591F32081F39707A3ADC1FEC4ED2D6DCE` |
| `navigation/zhishiku.svg` | 知识库（未选中） | S:32；N:98–106、1099；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/zhishiku.svg` | `F951EF75A6DA05E165900BA871A79C8ECC6499D3AA6B0C440461396CC4FFB5F4` |
| `navigation/zhishiku-green.svg` | 知识库（选中） | S:32；N:98–106、1099；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/zhishiku-green.svg` | `7062C888D761435CEA07E6E32D20F8A3889297979E6F7B249176259BD73539D2` |
| `navigation/agent.svg` | 智能体（未选中） | S:33；N:98–106、1102；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/agent.svg` | `B1040A1F5FB8DE02FEE4FF41F76FD87FC878ABC6E7F2E0660265171639AEDBA2` |
| `navigation/agent-green.svg` | 智能体（选中） | S:33；N:98–106、1102；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/agent-green.svg` | `DDEA3322C87ECD1EBEBB2111518AB23C56391FB66A74FDE56611D6519285ADCA` |
| `navigation/organization.svg` | 组织（未选中） | S:34；N:98–106、1105；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/organization.svg` | `5D0B60B3C4EB4C428E5BB3F3B559F458E435D62ED2B6CD28B2428E3DC0B26AFC` |
| `navigation/organization-green.svg` | 组织（选中） | S:34；N:98–106、1105；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/organization-green.svg` | `B2187547E0BB7BB5DCC63E1B6D7616FBF363A9728817DEBECE137D7E3A4ECB65` |
| `navigation/prefixIcon.svg` | 新建对话（未选中） | S:27；N:98–106、1108；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/prefixIcon.svg` | `EAE1AC0F091B54BD2412B3B0C4DCB39059BD07E57050AB29777309C1A81B4439` |
| `navigation/prefixIcon-green.svg` | 新建对话（选中） | S:27；N:98–106、1108；N:1185–1186；T:1145–1147 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/img/prefixIcon-green.svg` | `6CEB3B444929DD700368613CAA27ED5C7D4F310505ECED0026C49F23AEBB685F` |

### 品牌、欢迎与页面装饰：已核验引用

| 目标文件 | 用途／状态 | 当前引用点 | viewBox | 源文件（绝对路径） | SHA256（源＝目标） |
| --- | --- | --- | --- | --- | --- |
| `materials/iridescent-bow.svg` | 品牌、导航签名、欢迎页标题 | N:292 → 69；W:3 → 19；L:27 → 488 | `0 0 640 480` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/materials/iridescent-bow.svg` | `C3FB6F8811B6F2D4190C7FCD5D977116EBC8D3049653B3776BE0CB5DC882D8C9` |
| `materials/iridescent-ribbon.svg` | 欢迎页缎带、输入区点缀 | W:4 → 32；A:3 → 12；L:28 → 551 | `0 0 960 280` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/materials/iridescent-ribbon.svg` | `511ACB20CCA52118DB8DF0A9F011B8A71D8A7AE80D2B7D291C36AC9F9B582100` |
| `materials/clover-sprig.svg` | 导航签名、欢迎页四叶草 | N:291 → 75；W:6 → 15 | `0 0 400 500` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/materials/clover-sprig.svg` | `D408D605722123E9CFFA228AD11839285D657DD703BC344717D2CBEF63B85456` |
| `materials/sakura-branch.svg` | 欢迎页／登录页樱花枝 | W:5 → 14；L:25 → 553；C:309 | `0 0 760 540` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/materials/sakura-branch.svg` | `CD9BD4B201D456E66BA6BC92E26AF5477EF57735E29A688BCBD8B1139C656118` |
| `materials/clover-corner.svg` | 宽屏卡片或欢迎区角饰（原项目用于组织页） | C:281（媒体条件）、320–324、348 | `0 0 800 640` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/materials/clover-corner.svg` | `A2B24F26D248D87959230F0388B792D3F25CD2B76A4CB30E53D55CA9FBBA0EB1` |
| `materials/sakura-parasol.svg` | 欢迎／空状态插画（原项目用于登录页及空状态） | L:26 → 550；M:100；C:432 | `0 0 720 700` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/materials/sakura-parasol.svg` | `7D0A05B55D07ADA1AA9643C852DC14A1D404BA540192D98A53B5556800A3C783` |

### 现有蓝色功能图标：已核验非导航用途

| 目标文件 | 用途／状态 | 当前引用点 | viewBox | 源文件（绝对路径） | SHA256（源＝目标） |
| --- | --- | --- | --- | --- | --- |
| `icons/anna-agent-green.svg` | 现有蓝色智能体图标；来源用途为组织列表统计，不是当前侧栏加载文件 | T:1093–1094；O:153、404 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/icons/anna-agent-green.svg` | `0D396966A9D00C0D76F158B0A254BA72FD240EDF90653AC9E2A907EA4F93F775` |
| `icons/anna-organization-green.svg` | 现有蓝色组织图标；来源用途为详情元数据，不是当前侧栏加载文件 | T:1097–1099；G:168；H:92、232 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/icons/anna-organization-green.svg` | `9514D2BAD97C021C07FB8D71C3CB20DBF19333F496335BA0671E3C903C47D2BE` |

### 历史保留：没有找到当前明确运行时代码引用

| 目标文件 | 用途／状态 | 当前引用点 | viewBox | 源文件（绝对路径） | SHA256（源＝目标） |
| --- | --- | --- | --- | --- | --- |
| `icons/anna-integration-green.svg` | 仅保留原有文件；本次不推荐 | 未找到明确运行时代码引用；文件存在或生成脚本不算引用 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/icons/anna-integration-green.svg` | `8798E8894249C7A6129781226882D8E068439B52D300AA391BB5917D691374B9` |
| `icons/anna-search-green.svg` | 仅保留原有文件；本次不推荐 | 未找到明确运行时代码引用；文件存在或生成脚本不算引用 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/icons/anna-search-green.svg` | `349A1C37103209967B7C23858443D51F5DF992E8E3965E26E0F2712C75B946AA` |
| `icons/anna-setting-green.svg` | 仅保留原有文件；本次不推荐 | 未找到明确运行时代码引用；文件存在或生成脚本不算引用 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/icons/anna-setting-green.svg` | `BA28F8227C6A044901C12EF186BEEE397A904EAAAAE8484523E7AC8345B65040` |
| `icons/anna-soda-glass.svg` | 仅保留原有文件；本次不推荐 | 未找到明确运行时代码引用；文件存在或生成脚本不算引用 | `0 0 180 240` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/icons/anna-soda-glass.svg` | `727F4FE7784A69450735F20E1CADC8DF358A067F275D84E0E2A4968978792BE0` |
| `icons/anna-zhishiku-green.svg` | 仅保留原有文件；本次不推荐 | 未找到明确运行时代码引用；文件存在或生成脚本不算引用 | `0 0 20 20` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/icons/anna-zhishiku-green.svg` | `C13CDD586ECB30576FFBBDF801EEF3641389C749F41AA4681FECE43F43137518` |
| `icons/anna-ziliao.svg` | 仅保留原有文件；本次不推荐 | 未找到明确运行时代码引用；文件存在或生成脚本不算引用 | `0 0 16 16` | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/icons/anna-ziliao.svg` | `E9C0FBFB288B04339C78B417B4D4E081E8AFFF9EF66F063B550CF0D070CB0FD0` |

“未找到引用”的检索范围为来源 `frontend/src` 中 Vue、TS/TSX、JS/JSX、CSS/SCSS/Less、HTML，排除测试和工具脚本，按完整文件名检索；它不是对任意动态拼接或外部程序的不存在性证明。上述 6 个文件与来源同名文件的字节一致，仅此不足以证明当前使用；RAG4C 已有引用也不能反证 WeKnora 当前使用。

## 引用文件索引（绝对路径＋快照 SHA256）

| ID | 源码文件 | SHA256 |
| --- | --- | --- |
| E | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/index.html` | `D33CA6E83A6B478BED7E9D8C03240627D3709964AF9EAB9C747839629FF97659` |
| B | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/main.ts` | `E1324F320E260F46376872095179FA2B3C2DB4D19168E2F9BD51E8813D3C157A` |
| R | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/router/index.ts` | `059214B3DCF0D7D3913CD786F8FDD14FE61BA423F0D6D54F43E56C6B1980BFD4` |
| P | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/views/platform/index.vue` | `B2C02A493109C33178FD5DDB5D4258F692CD7EB8B81F66A51871854CDE1617AA` |
| Q | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/views/creatChat/creatChat.vue` | `A533DBFC682B69C9B0219E80BF7446ECC8443AF9C6CA6568F3EDE9D61F6035C3` |
| N | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/components/menu.vue` | `A0012009FFFC7659C09560036DF1C18FA5997E2601B7A0302BD703102D59F9C2` |
| S | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/stores/menu.ts` | `39F31D8D0E2BBE6D30B9B50447FACC2DCEEAD98D17268992D8C18A48C773C2C4` |
| W | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/components/theme/AnnaWelcome.vue` | `B6B4E625E85AD70BC76D58A2A7A402BC2D7E2F40ED1BC7AED7A070BD98C64A5D` |
| A | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/components/theme/AnnaComposerAccent.vue` | `629D05F2C3DC23BF5337B0705F5CBF09203BA468BEDA7BE93069F02B29C2454E` |
| T | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/anna-theme.css` | `CFD1B33D074928B9DA331C2BA137260816137F331DD8330176C7162E51B9754B` |
| M | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/anna-materials.css` | `FF88416A7B4F8A21FDA1D3153A3B25160CD8EA49404DC3E94285F99398521831` |
| C | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/anna-components.css` | `DEA4ACDFAB7A88079075607220B4230F8DCA8F7FAF3264D30C8A23C6D875DDA5` |
| D | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/assets/theme/anna/anna-desktop.css` | `A7B1ABEADF7800044FAE2429E6723F29454655A44381C082167CB9B0451C056E` |
| L | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/views/auth/Login.vue` | `086DA93849B67CF648F2E0ECA6DD13D63D8F236464D925335ABB88C87BC8AA87` |
| O | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/views/organization/OrganizationList.vue` | `0A3946D61E92B8ECA4881623833F26B0763D9ADCD54752105CD89B1D375CC4B2` |
| G | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/components/AgentSelector.vue` | `BC4A037227FE104DDFBC613FA40DC966BEB3A75117E92AA7CFD44EC43B4567D3` |
| H | `C:/Users/饶策/Desktop/me/WeKnora-0.8.0/frontend/src/components/MentionSelector.vue` | `2AA11EAE6EC94E17809AFA5DEA3161391D0F41DC09E32D7728EC169BECD04F20` |

## 建议如何引用（示例，不代表已修改 src）

公开 URL 是 `/themes/yanami/...`，不要带 `public`。导航建议 20×20，品牌蝴蝶结可从 38×26 起调，缎带和樱花枝按宽度缩放、保持原比例。装饰以外链 `<img>` 或 CSS 背景使用，不将多份 SVG 内部 ID 注入同一 DOM。

```tsx
// 在需要改动业务前端时再接入；本次仅交付素材及审计说明。
<img src="/themes/yanami/materials/iridescent-bow.svg" width={38} height={26} alt="" aria-hidden="true" />
<img src="/themes/yanami/materials/sakura-branch.svg" alt="" aria-hidden="true" />
<img
  className="yanami-nav-icon"
  src={active ? "/themes/yanami/navigation/zhishiku-green.svg" : "/themes/yanami/navigation/zhishiku.svg"}
  width={20}
  height={20}
  alt=""
  aria-hidden="true"
/>
```

```css
/* 仅供接入建议：保持原项目“选中态 SVG + 主题滤镜”的分工。 */
html[data-theme="anime"] [aria-current="page"] .yanami-nav-icon {
  filter: hue-rotate(53deg);
}
html[data-theme="anime"] .yanami-welcome-decoration {
  background: url("/themes/yanami/materials/iridescent-ribbon.svg") center / contain no-repeat;
  pointer-events: none;
}
```

导航名称由相邻可见文字或按钮可访问名称提供；独立承担品牌语义的图像另设合适 `alt`。不要给 `icons/anna-agent-green.svg` 等已是蓝色的版本再套上述选中态滤镜。建议只在 `anime` 皮肤启用，亮／暗主题保留原行为；正文、状态色、权限与路由不由装饰素材接管。

## 本地复核

以下 PowerShell 从本页清单读取全部 23 个 SVG 和 1 个生产 PNG 的目标路径、源路径及哈希，校验文件存在、源／目标 SHA256 一致以及 SVG 根节点或 PNG 文件头、字节数和尺寸；随后校验 17 份引用源码快照。任一项变化都会明确失败，不会将文件名匹配当成通过。

```powershell
$ErrorActionPreference = 'Stop'
$root = 'D:/program_project/python_project/RAG4C/frontend/public/themes/yanami'
$readme = Get-Content -LiteralPath (Join-Path $root 'README.md')
$svgCount = 0
$pngCount = 0
foreach ($line in $readme) {
    $cols = $line.Split('|')
    if ($cols.Length -ne 8) { continue }
    $relative = $cols[1].Trim().Trim([char]96)
    if ($relative -notmatch '^(navigation|materials|icons)/.+\.(svg|png)$') { continue }
    $source = $cols[5].Trim().Trim([char]96)
    $expected = $cols[6].Trim().Trim([char]96)
    $target = Join-Path $root $relative
    foreach ($path in @($source, $target)) {
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected) {
            throw "SHA256 mismatch: $path"
        }
    }
    if ($relative.EndsWith('.svg')) {
        [xml]$svg = Get-Content -LiteralPath $target -Raw
        if ($svg.DocumentElement.LocalName -ne 'svg') { throw "Not SVG: $target" }
        $svgCount++
    } else {
        $bytes = [System.IO.File]::ReadAllBytes($target)
        if ($bytes.Length -ne 869076 -or
            [BitConverter]::ToString($bytes[0..7]) -ne '89-50-4E-47-0D-0A-1A-0A' -or
            [BitConverter]::ToString($bytes[12..15]) -ne '49-48-44-52' -or
            [BitConverter]::ToString($bytes[16..23]) -ne '00-00-02-F6-00-00-05-00') {
            throw "PNG signature, dimensions or size mismatch: $target"
        }
        $pngCount++
    }
}
if ($svgCount -ne 23 -or $pngCount -ne 1) { throw "Unexpected asset counts: SVG=$svgCount PNG=$pngCount" }
if (Test-Path -LiteralPath (Join-Path $root 'characters/anna-iridescent-character.svg')) {
    throw 'Withdrawn character SVG must not be delivered'
}
$evidenceCount = 0
foreach ($line in $readme) {
    $cols = $line.Split('|')
    if ($cols.Length -ne 5 -or $cols[1].Trim() -notmatch '^[A-Z]$') { continue }
    $path = $cols[2].Trim().Trim([char]96)
    $expected = $cols[3].Trim().Trim([char]96)
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected) {
        throw "Evidence source changed; re-audit line references: $path"
    }
    $evidenceCount++
}
if ($evidenceCount -ne 17) { throw "Unexpected evidence count: $evidenceCount" }
"PASS: $svgCount SVG files, $pngCount production PNG (869076 bytes, 758x1280), $evidenceCount evidence snapshots"
```

素材原有权利与使用范围不因本次本地复制而改变。本次只审计技术引用来源，不作新的授权声明。
