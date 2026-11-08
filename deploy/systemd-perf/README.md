# Debian 13 共享 2 GiB 主机资源护栏

这里是**可回滚示例文件**，不会被 install.sh 自动安装。安装前先量测生产 RSS、PSS、SSE 并发峰值和用量记账。不要把生产域名、Key 或密钥文件写到 Git。示例不会覆盖 ExecStart、前端 web/out、Cloudflare Access、用量窗口、SSE 首事件或 15 秒 ping。

## 容量预算

| 服务 | MemoryHigh | MemoryMax | MemorySwapMax |
|---|---:|---:|---:|
| Manager | 224 MiB | 320 MiB | 128 MiB |
| wb2api | 128 MiB | 192 MiB | 64 MiB |
| cloudflared | 40 MiB | 64 MiB | 32 MiB |
| 合计 | 392 MiB | 576 MiB | 224 MiB |

这是初始硬上限和压力门槛，不是实测 RSS。Minecraft 的 Java 堆可能达到 1024 MiB，还需额外 native、线程栈和操作系统空间，Chromium 又可能消耗约 640 MiB。2 GiB 内存无法保证三者全峰值共存而不 OOM。资源上限能减少网关挤占 Minecraft 的风险，却不能取代按需关闭 Chromium。OOMScoreAdjust 提高网关被系统优先回收的概率，不是绝对保障。

## 第 0 步：采集基线

仅获取资源数字，禁止输出 Key、环境变量、账号凭据与原始日志内容。

    free -h
    cat /proc/pressure/memory
    for u in workbuddy-web workbuddy2api cloudflared; do
      echo "== $u =="
      systemctl show "$u" -p MainPID -p MemoryCurrent -p MemoryPeak -p MemoryHigh -p MemoryMax -p MemorySwapCurrent -p NRestarts
    done
    du -sh /opt/workbuddy-manager/venv /opt/workbuddy-manager/web/out /opt/workbuddy2api/auths 2>/dev/null
    du -h /opt/workbuddy-manager/data/manager.db* 2>/dev/null
    du -h /opt/workbuddy2api/data/server.*.log* 2>/dev/null
    journalctl --disk-usage

Manager 附带 tools/bench_gateway_sse.py：使用现有网关 Key 的环境变量或交互输入，测试 Chat、Responses 的首字时间、用量口径输出 token/s、帧数和心跳。只记录计数和时间，不记录请求/响应正文与凭据。可分别对本地 Manager 和 wb2api 端口压测并比较网关附加耗时。目标 340 token/s 受上游生成能力限制；合成 340 帧的 Go 基准不是实际模型 token/s。

## 安装示例

上游原 unit 模板名是 workbuddy-web.service；实际机器可能叫 workbuddy-manager.service，必须先用 systemctl 确认，按实际名称放置 drop-in，不要新建重复服务。

    sudo install -Dm0644 deploy/systemd-perf/workbuddy-web.service.d/50-resource-budget.conf /etc/systemd/system/workbuddy-web.service.d/50-resource-budget.conf
    sudo install -Dm0644 deploy/systemd-perf/workbuddy2api.service.d/50-resource-budget.conf /etc/systemd/system/workbuddy2api.service.d/50-resource-budget.conf
    sudo install -Dm0644 deploy/systemd-perf/cloudflared.service.d/50-resource-budget.conf /etc/systemd/system/cloudflared.service.d/50-resource-budget.conf
    sudo systemctl daemon-reload
    systemctl show workbuddy-web -p MemoryHigh -p MemoryMax
    systemctl show workbuddy2api -p MemoryHigh -p MemoryMax
    systemctl show cloudflared -p MemoryHigh -p MemoryMax

在低峰分别重启服务后验证限制是否生效。重启将中断现有 SSE。任何 cgroup OOM、延迟恶化或吞吐下降，应先降低并发或针对性放宽限制。特别是 MemoryHigh 会产生回收压力，不能盲目压低。

## 上游日志与账号回填

原生 wb2api 的 server.out.log 保存每次 Chat 请求的账号记录；server.err.log 保存任务日志。Manager 的 accountlog 通过这些信息回填 request_logs.account，不能直接关闭日志。

Manager 在 WB2API_MODE=systemd 下读取 journalctl，十五秒采样最多五百行。先确认 wb2api 的 stdout/stderr 真正被 systemd journal 接收，Manager 所用账号能读 journal，/api/logs/account-backfill 自检显示已解析和回填，然后才可以停用旧 append 文件。读取日志不等于具有 systemctl 管理服务权限；不要为了 UI 的启停功能给予无约束 sudo。

若超过 500 行/15 秒会漏掉旧行，要先改成游标消费或调整采集。journald.conf.d/40-workbuddy-budget.conf 仅作为**全局**磁盘预算示例，会影响整机其他服务，不能当作 wb2api 专属的日志轮转。采用 journal 后仍需检查是否有 shell 重定向在写旧文件。

## SQLite 与数据保存

原始 db.py 已包含 request_logs 90 天保留及每 2000 次写入触发清理，也包含 usage_hourly、task_logs 的 90 天和 audit_logs 20000 行限制。使用 WAL、NORMAL synchronous。先检查 manager.db、-wal 的实际体积和空闲页；不要重复实现清理器或每次请求 VACUUM。运行中只建议保守 checkpoint；完整 VACUUM 必须安排维护窗口并先确保现有加密备份有效。

manager.db、users.json、wb2api config.json、auths/、data/state.json 都属于关键持久化数据，备份必须加密并限制权限。venv、web/out、可重建 Go 二进制可按相应版本重新生成。不要把密钥写入新文本文件或 Git 仓库。

## 回滚

只删除本次安装的三个配置文件，别用会清除管理员其它配置的全量 systemctl revert：

    sudo rm -f /etc/systemd/system/workbuddy-web.service.d/50-resource-budget.conf
    sudo rm -f /etc/systemd/system/workbuddy2api.service.d/50-resource-budget.conf
    sudo rm -f /etc/systemd/system/cloudflared.service.d/50-resource-budget.conf
    sudo systemctl daemon-reload

在维护窗口逐一重启对应服务。如果启用了全局 journald 示例，需要单独移除它并重启 systemd-journald。回滚不更改网关 Key、域名、Workers、SQLite 或前端文件。


## 整机进一步优化与验收

1. 每次压测或迁移前后运行以下只读采样器。它不显示进程启动参数、环境变量、Key 或日志正文：

       sh tools/check_host_resources.sh workbuddy-web workbuddy2api cloudflared minecraft

   如果 Manager 的实际 unit 是 workbuddy-manager，替换对应参数。针对 Minecraft/Chromium 多进程，请重点看 systemd MemoryCurrent 和 cgroup oom_kill，而不仅是 MainPID 的 RSS。当前实测的 Manager 和 Go 空闲进程数据只来自无凭据的 CI 隔离环境，不代表生产账号池的整体占用。

2. Go drop-in 另提供 GOMEMLIMIT=144MiB。它是 Go runtime 的**软内存目标**，并非 RSS 硬上限。不能从这个值推断工作集上限；Go 的堆外、网络缓冲与服务其他资源仍可能使实际 RSS 更高。先测纯转发每流 token/s、CPU、延迟和 1M 上下文；若 GC 过于频繁导致延迟退化，可仅去掉该环境变量而保持其它 systemd 资源规则。

3. Manager drop-in 提供 MALLOC_ARENA_MAX=2，目标是降低大请求结束后的 glibc malloc arena 碎片保留，不保证影响 CPython 自己的全部小对象分配。对冷启动 RSS 可能没有可见影响。

4. 可选的 minecraft.service.d/50-minecraft-priority.conf 示例保护 Minecraft 常用内存并提高 CPU/IO 相对权重，**不会更改原有 Java 堆参数**。仅在实际服务名确认后手动安装；不要直接设置 MemoryMin（硬保护可能把全局 OOM 推给其它服务）。MemoryLow 保护也不能凭空创造内存。

5. Chromium 要在使用结束后退出**整个进程树**，而不只是主 PID。若已有自动关闭逻辑，不要另建定时任务；在真实 VPS 中确认 scope 的 memory.current 接近 0。不要在内存不足时同时压测 Chromium、Minecraft 满堆和大上下文。

6. SQLite 可选变量 WB_REQUEST_LOG_RETAIN_DAYS=14（允许 7–365 天，默认 90 天不变），缩短 request_logs 和账号匹配明细的保留期。**注意这不是无损压缩**：usage_daily 与 Key 已用额度等汇总数据不因此清零，但较早的逐笔明细会被永久删除，原始逐笔日志重建能力随之缩短。启用前先完成加密备份，且确认管理界面只需要更短的明细窗口；绝不在未确认时自动开启。
