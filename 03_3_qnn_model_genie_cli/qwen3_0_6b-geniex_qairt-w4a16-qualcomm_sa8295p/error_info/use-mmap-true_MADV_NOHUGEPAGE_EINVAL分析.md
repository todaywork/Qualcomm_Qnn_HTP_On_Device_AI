# SA8295P `use-mmap=true` 模型加载失败分析

## 1. 结论

SA8295P 设备使用 `use-mmap=true` 加载 Qwen3-0.6B HTP V68 模型时失败，直接原因是：

1. Genie 成功使用 `mmap()` 映射 context binary。
2. Genie 随后调用 `madvise(..., MADV_NOHUGEPAGE)`。
3. SA8295P 的 Linux 5.4 内核没有启用 `CONFIG_TRANSPARENT_HUGEPAGE`。
4. 该内核不把 `MADV_NOHUGEPAGE` 识别为合法 advice，因此返回 `EINVAL`。
5. QAIRT 2.45 Genie Android 路径将这个非关键的内存建议失败当成致命错误，直接返回 `false`。
6. 模型因此在读取第一个 context binary 的元信息阶段终止，尚未进入 HTP graph/context 创建。

这不是模型 BIN 损坏、文件权限、SELinux、地址未对齐或普通内存不足导致的错误。

## 2. 测试环境

### 2.1 失败设备

```text
ADB serial      : 192.168.43.5:5555
Product model   : L946
SoC             : SA8295P
Board platform  : msmnile
Android         : 12 / SDK 32
Kernel          : Linux 5.4.219-qgki-gaf638c0d56fb
HTP architecture: V68
SELinux         : Permissive
Model target    : qualcomm-sa8295p
soc_model       : 39
QAIRT           : 2.45.0.260326154327
Genie           : 1.17.0
```

### 2.2 成功对照设备

```text
ADB serial      : a19e685a
Product model   : gen4_gvm for arm64
ro.soc.model    : SA8255P
Android         : 14 / SDK 34
Kernel          : Linux 6.1.99-android14-11
HTP model target: qualcomm-sa8775p / V73
SELinux         : Permissive
QAIRT           : 2.45.0.260326154327
Genie           : 1.17.0
```

两个设备上的以下公共运行时 SHA256 完全一致：

```text
genie-t2t-run
libGenie.so
libQnnHtp.so
libQnnSystem.so
```

两个设备使用各自匹配的 context BIN、HTP Stub 和 Skel；成功设备为 V73，失败设备为 V68。

## 3. Genie CLI 复现

### 3.1 配置关键项

```json
{
  "QnnHtp": {
    "use-mmap": true
  }
}
```

### 3.2 复现命令

```sh
cd /data/local/tmp/genie_qwen3_quality

export LD_LIBRARY_PATH=$PWD/genie_cli/libs:$PWD/dsp:/vendor/lib64:/system/lib64
export ADSP_LIBRARY_PATH="/vendor/lib/rfsa/adsp;$PWD;$PWD/dsp;$PWD/lib"
export CDSP_LIBRARY_PATH="$ADSP_LIBRARY_PATH"
export CDSP1_LIBRARY_PATH="$ADSP_LIBRARY_PATH"

./genie_cli/bin/genie-t2t-run \
  --config genie_config.json \
  --prompt_file genie_cli/genie_cli_prompt.txt \
  --log verbose \
  --profile /data/local/tmp/genie_compare_profile.json
```

### 3.3 SA8295P 实际输出

```text
Using libGenie.so version 1.17.0

[ERROR] "Failed to advise OS on memory usage err: Invalid argument"
[ERROR] "Failed to map context Binary for contextIdx: 0"
Failure to initialize model.
Failed to create the dialog.
```

错误发生在 `contextIdx: 0` 的元信息读取阶段。

## 4. 两台设备的 THP 配置差异

检查命令：

```sh
zcat /proc/config.gz | grep TRANSPARENT_HUGEPAGE
ls -ld /sys/kernel/mm/transparent_hugepage
```

### 4.1 成功设备 a19e685a

```text
CONFIG_HAVE_ARCH_TRANSPARENT_HUGEPAGE=y
CONFIG_TRANSPARENT_HUGEPAGE=y
CONFIG_TRANSPARENT_HUGEPAGE_MADVISE=y
```

并且存在：

```text
/sys/kernel/mm/transparent_hugepage
```

因此该内核支持 `MADV_NOHUGEPAGE`。

### 4.2 失败设备 SA8295P

```text
CONFIG_HAVE_ARCH_TRANSPARENT_HUGEPAGE=y
# CONFIG_TRANSPARENT_HUGEPAGE is not set
```

并且不存在：

```text
/sys/kernel/mm/transparent_hugepage
```

`CONFIG_HAVE_ARCH_TRANSPARENT_HUGEPAGE=y` 只表示 CPU 架构具备实现 THP 的能力；真正决定内核是否编译 THP 功能的是 `CONFIG_TRANSPARENT_HUGEPAGE`。本设备没有启用后者。

### 4.3 一键检测当前系统是否仍存在 THP 不支持问题

在 Windows PowerShell 或 CMD 中执行以下命令。该命令会同时输出设备身份、Android/内核版本、内核 THP 配置、THP sysfs 节点、当前运行模式，并给出自动判定结果：

```powershell
adb shell "echo '=== DEVICE ==='; echo -n 'Model: '; getprop ro.product.model; echo -n 'SoC: '; getprop ro.soc.model; echo -n 'Platform: '; getprop ro.board.platform; echo -n 'Android: '; getprop ro.build.version.release; echo -n 'Kernel: '; uname -r; echo '=== KERNEL CONFIG ==='; if [ -r /proc/config.gz ]; then zcat /proc/config.gz | grep TRANSPARENT_HUGEPAGE; if zcat /proc/config.gz | grep -q '^CONFIG_TRANSPARENT_HUGEPAGE=y'; then echo 'CONFIG_RESULT: THP_COMPILED'; elif zcat /proc/config.gz | grep -q '^# CONFIG_TRANSPARENT_HUGEPAGE is not set'; then echo 'CONFIG_RESULT: THP_NOT_COMPILED'; else echo 'CONFIG_RESULT: THP_CONFIG_UNKNOWN'; fi; else echo 'CONFIG_RESULT: CONFIG_GZ_UNAVAILABLE'; fi; echo '=== THP SYSFS ==='; if [ -d /sys/kernel/mm/transparent_hugepage ]; then echo 'SYSFS_RESULT: THP_SUPPORTED'; echo -n 'Enabled: '; cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null; echo -n 'Defrag: '; cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null; else echo 'SYSFS_RESULT: THP_NOT_SUPPORTED'; fi; echo '=== FINAL VERDICT ==='; if [ -d /sys/kernel/mm/transparent_hugepage ]; then echo 'THP_CAUSE_RESULT: NOT_PRESENT'; echo 'THP support exists; MADV_NOHUGEPAGE should normally be accepted.'; else echo 'THP_CAUSE_RESULT: PRESENT_OR_LIKELY'; echo 'THP support is absent; MADV_NOHUGEPAGE may return EINVAL.'; fi"
```



```
8255输出内容如下：
=== DEVICE ===
Model: gen4_gvm for arm64
SoC: SA8255P
Platform: gen4
Android: 14
Kernel: 6.1.99-android14-11-maybe-dirty
=== KERNEL CONFIG ===
CONFIG_HAVE_ARCH_TRANSPARENT_HUGEPAGE=y
CONFIG_TRANSPARENT_HUGEPAGE=y
# CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS is not set
CONFIG_TRANSPARENT_HUGEPAGE_MADVISE=y
CONFIG_RESULT: THP_COMPILED
=== THP SYSFS ===
SYSFS_RESULT: THP_SUPPORTED
Enabled: always [madvise] never
Defrag: always defer defer+madvise [madvise] never
=== FINAL VERDICT ===
THP_CAUSE_RESULT: NOT_PRESENT
THP support exists; MADV_NOHUGEPAGE should normally be accepted.

#SA8295输出：
Model: L946
SoC: SA8295P
Platform: msmnile
Android: 12
Kernel: 5.4.219-qgki-gaf638c0d56fb
=== KERNEL CONFIG ===
CONFIG_HAVE_ARCH_TRANSPARENT_HUGEPAGE=y
# CONFIG_TRANSPARENT_HUGEPAGE is not set
CONFIG_RESULT: THP_NOT_COMPILED
=== THP SYSFS ===
SYSFS_RESULT: THP_NOT_SUPPORTED
=== FINAL VERDICT ===
THP support absent: MADV_NOHUGEPAGE may return EINVAL.
```

对输出内容详细解释：

```plain
CONFIG_HAVE_ARCH_TRANSPARENT_HUGEPAGE=y     ← ARM64 架构支持 THP
CONFIG_TRANSPARENT_HUGEPAGE=y               ← THP 功能已编译进内核
# CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS is not set   ← 未强制 always 模式
CONFIG_TRANSPARENT_HUGEPAGE_MADVISE=y       ← 编译默认策略为 madvise
CONFIG_RESULT: THP_COMPILED                 ← 综合结论：THP 已编译
```

## 与之前那台 SA8255P 的对比

| 维度                | SA8255P（上一台）                 | SA8295P（这一台）     |
| :------------------ | :-------------------------------- | :-------------------- |
| **Android**         | 14                                | 12                    |
| **Kernel**          | 6.1                               | 5.4                   |
| **环境**            | GVM 虚拟机                        | 疑似物理机/不同虚拟化 |
| **THP 编译**        | ✅ `CONFIG_TRANSPARENT_HUGEPAGE=y` | ❌ 未设置              |
| **THP 运行**        | ✅ `madvise` 模式可用              | ❌ 完全不存在          |
| **MADV_NOHUGEPAGE** | ✅ 安全调用                        | ⚠️ **会返回 `EINVAL`** |
| **MADV_HUGEPAGE**   | ✅ 可用                            | ❌ 不可用              |



## 5. QAIRT 2.45 Genie 源码调用链

本机 QAIRT 源码：

```text
E:\QualComm\AIStack\QAIRT\2.45.0.260326\examples\Genie\Genie\src\qualla
```

### 5.1 创建文件映射

文件：

```text
MmappedFile\src\MmappedFile.cpp
```

核心代码：

```cpp
const int prot  = readwrite ? PROT_READ | PROT_WRITE : PROT_READ;
const int flags = readwrite ? MAP_SHARED : MAP_PRIVATE;
addr = mmap(nullptr, size, prot, flags, m_fileDescriptor, 0);
```

本次映射为：

```text
PROT_READ | MAP_PRIVATE
```

CLI 没有报告 `Failed to allocate memory mapped region`，说明 `mmap()` 本身已经成功。

### 5.2 对映射区域调用 MADV_NOHUGEPAGE

文件：

```text
engines\qnn-api\QnnApi.cpp:867-902
```

核心代码：

```cpp
auto mmf = std::make_shared<mmapped::File>(binaryPath);

if (!(*mmf)) {
    QNN_ERROR("Failed to allocate memory mapped region for context index = %zu", contextIdx);
}

if (!mmf->adviseRange(0, bufferSize, MADV_NOHUGEPAGE)) {
    QNN_ERROR("Failed to advise OS on memory usage err: %s", strerror(errno));
    return false;
}
```

`adviseRange()` 最终执行：

```cpp
madvise(range.first, range.second, MADV_NOHUGEPAGE);
```

### 5.3 地址和长度检查

`MmappedFile::getRange()` 会执行以下处理：

```cpp
if (reinterpret_cast<std::uintptr_t>(data()) % pageSize != 0) {
    return {nullptr, 0};
}

const auto start = ((offset + (pageSize - 1)) / pageSize) * pageSize;
const auto stop  = ((offset + length) / pageSize) * pageSize;
```

设备页大小：

```text
4096 bytes
```

因此传给 `madvise()` 的起始地址和长度已经按页处理，可以排除未对齐导致的 `EINVAL`。

## 6. Linux 5.4 为什么返回 EINVAL

Android 12 Linux 5.4 的 `mm/madvise.c` 中，合法 advice 列表包含：

```c
#ifdef CONFIG_TRANSPARENT_HUGEPAGE
case MADV_HUGEPAGE:
case MADV_NOHUGEPAGE:
#endif
```

如果没有编译 `CONFIG_TRANSPARENT_HUGEPAGE`，这两个 `case` 不会存在。`MADV_NOHUGEPAGE` 会进入：

```c
default:
    return false;
```

随后 `do_madvise()` 返回：

```text
-EINVAL
```

对应用户空间：

```text
errno = EINVAL
strerror(errno) = "Invalid argument"
```

Android 官方内核源码：

```text
https://android.googlesource.com/kernel/common/+/refs/tags/android12-5.4.296_r00/mm/madvise.c#849
```

## 7. 为什么这个失败不应该终止模型加载

`MADV_NOHUGEPAGE` 只是一条内存管理建议：告诉内核不要为对应 VMA 使用透明大页。它不会改变 context binary 的内容和正确性。

SA8295P 内核没有编译 THP，因此实际状态本来就是：

```text
该映射不可能使用 Transparent Huge Page
```

所以在这个设备上，无法设置 `MADV_NOHUGEPAGE` 不会带来功能性问题。正确行为应为记录警告并继续。

Linux 内核源码也将 `madvise` 描述为 advisory information，可以在不影响应用正确性的情况下忽略：

```text
https://android.googlesource.com/kernel/common/+/refs/tags/android12-5.4.296_r00/mm/madvise.c#896
```

## 8. Genie 不同平台分支的处理不一致

QAIRT 2.45 的 Linux OE/OpenWrt 分支：

```cpp
if (madvise(mmbuf, bufferSize, MADV_NOHUGEPAGE)) {
    QNN_WARN("Failed to advise OS on memory usage");
}

// 继续加载
```

Android 所走的通用分支：

```cpp
if (!mmf->adviseRange(0, bufferSize, MADV_NOHUGEPAGE)) {
    QNN_ERROR(...);
    return false;
}
```

也就是同一个 advisory 调用：

```text
Linux OE/OpenWrt：失败后警告并继续
Android          ：失败后直接终止加载
```

这是 Genie Android 路径对不支持 THP 内核的兼容性处理问题。

本机安装的 QAIRT 2.46.0 和 2.48.40 源码仍保留相同的 `return false`，因此单纯升级到这些版本不能保证解决。

排查完成。两台设备的差异已经比较明确：**根因不是 CMA 大小，而是内核 THP 能力和 FastRPC 驱动/传输能力不同。**



### 不同设备关键差异

| 项目             | `a19e685a` 成功设备       | SA8295P 失败设备      |
| ---------------- | ------------------------- | --------------------- |
| Android          | 14 / SDK 34               | 12 / SDK 32           |
| 内核             | 6.1.99                    | 5.4.219               |
| `/data` 文件系统 | F2FS                      | EXT4                  |
| HTP 模型         | SA8775P、V73              | SA8295P、V68          |
| Context 配置     | 1024                      | 4096，512 也验证过    |
| 内存接口         | DMA-BUF Heap              | 旧 ION                |
| FastRPC          | Hybrid FastRPC `hfastrpc` | 内置 `virtio_fastrpc` |
| THP              | 已启用                    | 未启用                |
| CLI 加载结果     | 成功                      | 失败                  |

虽然 `a19e685a` 的 `ro.soc.model` 显示 `SA8255P`，但它目录内实际模型配置是：



## 9. 排除项

### 9.1 不是 context binary 损坏

两个 BIN 均可被 QAIRT 2.45 `qnn-context-binary-utility` 正常解析：

```text
part1_of_2.bin buildId: v2.45.0.260326154327
part2_of_2.bin buildId: v2.45.0.260326154327
Core API version       : 2.34
Backend API version    : 5.45
```

### 9.2 不是 QNN 公共库版本错配

模型目录、CLI 和 APK 使用的 QAIRT/Genie/QNN 公共运行库已核对，`libQnnSystem.so` 等关键文件 SHA256 匹配。

### 9.3 不是普通内存不足

普通内存不足通常表现为 `ENOMEM`，本次 `madvise()` 返回的是 `EINVAL`。同时设备仍有约 10 GB `MemAvailable`。

### 9.4 不是文件系统或文件权限

文件已经成功 `mmap()`。如果文件无法打开或映射，应在创建 `mmapped::File` 时失败，而不是到 `MADV_NOHUGEPAGE` 才返回 `EINVAL`。

由于内核在 advice 合法性检查时就返回，换到 tmpfs、修改权限或修改 SELinux 不会解决本错误。

## 10. 修复建议

### 10.1 推荐：修改 Genie 错误处理

最小修复方式是把失败改为警告：

```cpp
if (!mmf->adviseRange(0, bufferSize, MADV_NOHUGEPAGE)) {
    QNN_WARN("Failed to advise OS on memory usage err: %s; continuing", strerror(errno));
}
```

或者只忽略明确表示功能不支持的错误：

```cpp
if (!mmf->adviseRange(0, bufferSize, MADV_NOHUGEPAGE)) {
    const int adviseErrno = errno;
    if (adviseErrno != EINVAL && adviseErrno != ENOSYS) {
        QNN_ERROR("Failed to advise OS on memory usage err: %s", strerror(adviseErrno));
        return false;
    }

    QNN_WARN("MADV_NOHUGEPAGE is unsupported by this kernel; continuing");
}
```

修改后需要重新编译并替换 `libGenie.so`，然后继续验证 mmap 路径是否能够完成两个 context 的创建。

### 10.2 BSP 方案：启用 THP

可在 SA8295P 内核中启用：

```text
CONFIG_TRANSPARENT_HUGEPAGE=y
```

但这需要重新编译和发布 BSP，并可能改变整机内存行为。仅为接受一条可忽略的 `MADV_NOHUGEPAGE` 建议而启用 THP，改动范围过大，不作为首选。

当前内核是编译时关闭 THP，不能通过运行时写 sysfs 临时开启；相关 sysfs 节点不存在。

### 10.3 不推荐只改为 use-mmap=false

`use-mmap=false` 可以绕过 `MADV_NOHUGEPAGE`，但 SA8295P 随后出现另一条独立问题：

```text
Allocated total size = 275120640 across 3 buffers
Could not create context from binary for context index = 1 : err 1002
```

内核日志：

```text
virtio_fastrpc: message is too big (187470)
virtio_fastrpc: fastrpc_internal_mem_map failed to map fd 42 flags 3 err -12
```

因此 `use-mmap=false` 不是当前 SA8295P 平台的完整解决方案。

## 11. 建议验证顺序

1. 修改 Genie，使 `MADV_NOHUGEPAGE` 的 `EINVAL/ENOSYS` 不再导致返回失败。
2. 重新编译 `libGenie.so`。
3. 保持 `use-mmap=true`，用相同 CLI 命令加载 SA8295P V68 模型。
4. 确认日志不再出现 `Failed to map context Binary for contextIdx: 0`。
5. 继续观察是否能完成 `QnnContext_createFromBinary()`。
6. 同时抓取 `genie-t2t-run --log verbose`、logcat 和包含 `fastrpc/QNN/HTP` 的 dmesg。

## 12. 最终错误链

```text
genie_config.json: use-mmap=true
    ↓
MmappedFile 使用 MAP_PRIVATE + PROT_READ 成功映射 part1_of_2.bin
    ↓
Genie 调用 madvise(mapping, aligned_length, MADV_NOHUGEPAGE)
    ↓
SA8295P 内核未编译 CONFIG_TRANSPARENT_HUGEPAGE
    ↓
madvise_behavior_valid() 不接受 MADV_NOHUGEPAGE
    ↓
内核返回 -EINVAL
    ↓
Genie 输出 Invalid argument
    ↓
QnnApi::mapAndGetContextBinaryInfo() 返回 false
    ↓
Failed to map context Binary for contextIdx: 0
    ↓
Failure to initialize model / Failed to create the dialog
```



