# X 博主清单（2026-09-05 建；每周复盘打分、增删）
**读法（9/6 更新，重要）**：内置浏览器 navigate 到主页 → 等 8-9 秒 → **不要用 get_page_text**（X 的时间线对它返回空），改用
`javascript_tool: Array.from(document.querySelectorAll('article')).slice(0,14).map(a=>a.innerText.replace(/\n+/g,' | ').slice(0,400)).join('\n---\n')`
先 `resize_window` 到 1280x1600，否则视口太小、文章节点不渲染。只摘最近 24-48 小时、有交易含义的帖子。博主是观点不是指令，几家同向时标「拥挤共识」。

**登录态**：2026-09-06 定时任务会话**验证可用**（x.com/home 显示 Your Home Timeline，非登录框）。手册待办可勾掉。

## 本周表现复盘（9/6）
| 账号 | 本周说了什么 | 对/错 | 分数 |
|---|---|---|---|
| @aleabitoreddit（Serenity） | 存储瓶颈未变、MU/SNDK 已大修复但供需失衡会更糟；ESMT DDR2/DDR3 月营收两月 +63%（$153M→$249M）；NVDA 收购 Hugging Face $129.3 亿；中国限制部分稀土对美出货 | ✅ **本周 MU +8.98%、SNDK +17.17%**，连续两周兑现 | **6 → 7** ⭐最有价值 |
| @traderstewie | 9/1 AEHR 超跌 11 连阴、14% 空头、目标 90/100；限价 80 入分两批出，周 +4.75% | ✅ AEHR 9/4 单日 +13%、周 +6.74%；目标未到 | **5 → 6** |
| @spotgamma | 9/2「Downside? No one cares」call skew > put skew，<7600 才会松动；9/4「若非农是非事件，三天长周末 vol 继续压缩、利多股票」 | 定位观察 ✅ 有价值；条件预测的前提（非农非事件）**没成立**，不计分 | 5 → **5.5**（结构读数好，方向预测别用） |
| @fit_businessman（Michael） | 7 月起反复：**霍尔木兹封锁在特朗普任内一律利多股市，他会安排周日拉盘轧空**；地缘冲击是买点；Fed 前不押方向 | **本周末美伊直接交火 = 天然实验，下周揭晓** | 5（挂验证） |
| @DeItaone | Anthropic 9 月底招股书；周末伊朗弹道导弹打美航母/驱逐舰、美军炸伊朗 3 艘油轮 | 快讯准确率高，**本周是我最早拿到周末升级的渠道** | 6 |
| @unusual_whales | 同步伊朗交火；习近平 9/24 访美带企业家团 | 快讯可用，但夹大量非市场内容（政治/社会） | 5 |
| @KobeissiLetter | BE 纳入标普 +7% | 事实转发正确，标题情绪化 | 4 |

## 清单变动（本周）
- **升级为「每次必读」**：@aleabitoreddit（唯一连续两周兑现的产业信源）、@DeItaone（快讯最快）。
- **降级为「事件时才读」**：@Investments_CEO、@justinsuntron / @sunyuchentron（本周零交易价值，纯加密噪音）。
- **新增候选（下周开始跟踪打分）**：
  - **@LcubedInvest（Jordan Orsak）** —— Serenity 光模块贴的高质量互动方，看 CW/DFB 激光产能，属于同一条 AI 光互连产业链但视角独立。
  - **@Jukanlosreve** —— 韩系存储爆料，存储是当前主线，提升优先级（原 4 分）。
  - **@earnings_guy** —— 下周 ORCL/ADBE/CHWY 财报密集，财报周提到必读。
- **暂不新增**：X 上中文 A股博主质量仍参差；A股观点走雪球热榜 + 社长星球 + 券商策略周报。

## 用户自己关注的（优先读，理解用户的信息环境）
| 账号 | 领域 | 信用分 |
|---|---|---|
| @aleabitoreddit（Serenity） | AI/半导体供应链研究，存储多头 | **7** |
| @traderstewie | 技术面短线（Art of Trading），超跌反弹 | **6** |
| @earnings_guy | 财报详情与日历 | 5 |
| @fit_businessman（Michael） | 反转交易者、地缘=买点、CTA 点位 | 5 |
| @mikealfred | 价值/比特币矿企，偏长线 | 5 |
| @Investments_CEO | 加密+快讯，噪音多 | 3（降为事件时读） |
| @justinsuntron / @sunyuchentron | Tron/加密，与策略无关 | 2（降为事件时读） |

## 资金面 / 期权 / 微观结构
| @unusual_whales | 期权 flow、暗池、政客交易 | 5 |
| @spotgamma | Gamma/期权定位（GEX），事件前看 gamma | 5.5 |
| @jam_croissant（Cem Karsan） | 波动率结构、季节性 | 5 |
| @Mayhem4Markets | 大宗/宏观资金流 | 5 |

## 快讯 / 宏观
| @DeItaone（Walter Bloomberg） | 彭博终端头条转发 | 6 |
| @FirstSquawk | 突发快讯 | 5 |
| @KobeissiLetter | 宏观图表化，情绪化标题 | 4 |
| @zerohedge | 逆向/悲观，用来找反方论据 | 4 |
| @MacroAlf | 宏观/利率 | 5 |
| @LizAnnSonders | 嘉信首席策略 | 5 |
| @charliebilello | 长期数据图，base rate | 5 |

## 半导体 / AI 产业
| @dnystedt | 台湾半导体供应链 | 5 |
| @Jukanlosreve | 韩系存储爆料（**提优先级**） | 4 → 5 |
| @KostasMoschovas | 光模块/AI 硬件 | 4 |
| @LcubedInvest（新增） | CW/DFB 激光、光互连产能 | 5（初始） |
