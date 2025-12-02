import os
import re
import time
import subprocess
import sys

# 定义平台特定的subprocess创建标志，避免弹出控制台窗口
if sys.platform == 'win32':
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0
from typing import Callable, List, Dict, Optional

# 国内常用的pip镜像源（供UI使用）
PYPI_MIRRORS = {
    '阿里云': 'https://mirrors.aliyun.com/pypi/simple/',
    '清华大学': 'https://pypi.tuna.tsinghua.edu.cn/simple/',
    '中科技': 'https://pypi.mirrors.ustc.edu.cn/simple/',
    '豆瓣': 'https://pypi.douban.com/simple/',
    '华为云': 'https://mirrors.huaweicloud.com/repository/pypi/simple/',
    '腾讯云': 'https://mirrors.cloud.tencent.com/pypi/simple/'
}


class ComfyVenvTools:
    """
    后端工具类：承载环境检测、安装、查询等逻辑。
    目前为占位实现，返回可读信息，以便先搭建UI再逐步完善。
    """

    def __init__(self, logger: Callable[[str], None] | None = None):
        self.log = logger or (lambda s: None)
        # 记录最近一次的Python解释器与镜像选择，便于未传参的方法复用
        self._last_python_exe: Optional[str] = None
        self._last_mirror_name: Optional[str] = None
        # 添加已安装包缓存，避免重复查询
        self._installed_packages_cache: Optional[set[str]] = None
        self._cache_timestamp: float = 0.0
        self._cache_timeout: float = 30.0  # 缓存30秒

    # ---------------------- 镜像与环境 ----------------------
    def test_mirror_speed(self, python_exe: str, mirror_name: str) -> str:
        """测试单个镜像源响应速度。
        优先尝试HTTP连通性，其次使用 pip --dry-run 进行安装模拟以测延迟。
        """
        self._last_python_exe = python_exe or self._last_python_exe
        self._last_mirror_name = mirror_name or self._last_mirror_name
        url = PYPI_MIRRORS.get(mirror_name, '')
        start = time.time()
        # 1) HTTP连通性测试（HEAD）
        if url:
            try:
                import urllib.request, ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(url, method='HEAD')
                with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                    if resp.status < 400:
                        elapsed = time.time() - start
                        return f"镜像 {mirror_name} 连通性良好，响应时间 {elapsed:.2f}s"
            except Exception:
                # 回退到pip测试
                pass
        # 2) pip --dry-run 测试
        try:
            py = python_exe or 'python'
            cmd: List[str] = [py, '-m', 'pip', 'install', '--dry-run', 'pip']
            if url:
                host = url.split('/')[2]
                cmd += ['--index-url', url, '--trusted-host', host]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', timeout=20, creationflags=CREATE_NO_WINDOW)
            elapsed = time.time() - start
            if proc.returncode == 0:
                return f"镜像 {mirror_name} 测试成功，响应时间 {elapsed:.2f}s"
            else:
                out = (proc.stdout or '').strip().splitlines()
                return f"镜像 {mirror_name} 测试失败（{elapsed:.2f}s）: {out[:3]}"
        except subprocess.TimeoutExpired:
            return f"镜像 {mirror_name} 测试超时 (>20s)"
        except Exception as e:
            return f"镜像 {mirror_name} 测试异常: {e}"

    def set_python_env(self, python_exe: str) -> str:
        """设置后端当前Python环境以便后续操作复用。"""
        # 如果环境发生变化，清除缓存
        if python_exe and python_exe != self._last_python_exe:
            self._installed_packages_cache = None
            self._cache_timestamp = 0.0
        self._last_python_exe = python_exe or self._last_python_exe
        return f"[环境] 已设定Python: {self._last_python_exe or ''}"

    def set_mirror(self, mirror_name: str) -> str:
        """设置后端当前镜像选择以便依赖检查、安装时复用。"""
        self._last_mirror_name = mirror_name or self._last_mirror_name
        return f"[环境] 已设定镜像: {self._last_mirror_name or ''}"

    # ---------------------- 依赖与环境 ----------------------
    def check_dependencies(self, requirements_path: str, python_exe: str, plugin_dir: str,
                           progress_cb: Optional[Callable[[float], None]] = None) -> str:
        """检查依赖文件中哪些库已安装、哪些未安装（不进行模拟安装）。
        要求：requirements_path 已选中且 plugin_dir 与 python_exe 环境根一致。
        支持 progress_cb 实时反馈 0.0~1.0 进度。"""
        if not requirements_path:
            return "[依赖检查] 请先在下拉列表选择要检查的依赖文件"
        if not os.path.isfile(requirements_path):
            return "[依赖检查] 依赖文件不存在，请重新选择"
        if not self._same_env_root(python_exe, plugin_dir):
            return "[依赖检查] 插件目录与当前 Python 环境根不一致，请切换到对应环境或重新检测"

        # 记录最近一次的依赖文件，便于 simulate_install 复用
        self._last_requirements_path = requirements_path

        py = python_exe or self._last_python_exe or 'python'
        
        # 优化：一次性获取所有已安装的包，而不是逐个检查
        start_time = time.time()
        installed_packages = self._get_installed_packages_batch(py, progress_cb)
        
        # 解析依赖项并分类
        deps = self._parse_dependencies(requirements_path)
        installed: List[str] = []
        missing: List[str] = []
        git_specs: int = 0
        
        total = max(1, len(deps))
        for idx, spec in enumerate(deps):
            if spec == 'git+':
                git_specs += 1
                continue
            name = spec
            normalized_name = self._normalize_package_name(name)
            if normalized_name in installed_packages:
                installed.append(name)
            else:
                # 回退到 pip show 双形态检查（下划线与连字符）
                try:
                    hyphen_name = normalized_name
                    underscore_name = name.lower()
                    is_installed = self._is_package_installed(py, hyphen_name) or self._is_package_installed(py, underscore_name)
                except Exception:
                    is_installed = False
                if is_installed:
                    installed.append(name)
                else:
                    missing.append(name)
                
            # 减少进度更新频率，提高性能
            if progress_cb and idx % 10 == 0:
                try:
                    progress_cb(min(1.0, (idx + 1) / total) * 0.8 + 0.2)
                except Exception:
                    pass

        elapsed = time.time() - start_time
        lines: List[str] = []
        lines.append(f"[依赖检查] 文件: {os.path.basename(requirements_path)}  (Git源项: {git_specs})")
        lines.append(f"检查耗时: {elapsed:.2f}秒")
        lines.append(f"已安装: {len(installed)} 项")
        if installed:
            lines.extend([f"  - {n}" for n in installed[:200]])
        lines.append(f"未安装: {len(missing)} 项")
        if missing:
            lines.extend([f"  - {n}" for n in missing[:200]])

        return "\n".join(lines)

    def compute_missing_specs(self, requirements_path: str, python_exe: str, plugin_dir: str) -> List[str]:
        """返回原始规格格式（含版本）的未安装依赖列表。"""
        if not requirements_path or not os.path.isfile(requirements_path):
            return []
        if not self._same_env_root(python_exe, plugin_dir):
            return []
        py = python_exe or self._last_python_exe or 'python'
        specs = self._parse_dependencies(requirements_path)
        
        # 优化：批量获取已安装包
        installed_packages = self._get_installed_packages_batch(py)
        
        missing_specs: List[str] = []
        for spec in specs:
            if spec == 'git+':
                # git 来源不做安装状态检查，这里也跳过
                continue
            
            # 提取包名进行快速检查
            package_name = self._extract_name_from_spec(spec)
            if package_name:
                # 标准化包名进行匹配，处理下划线vs连字符的问题
                normalized_name = self._normalize_package_name(package_name)
                if normalized_name not in installed_packages:
                    # 回退到 pip show 双形态检查
                    if not (self._is_package_installed(py, normalized_name) or self._is_package_installed(py, package_name.lower())):
                        missing_specs.append(spec)
            else:
                # 如果无法提取包名，回退到原始方法
                try:
                    if not self._is_package_installed(py, spec):
                        missing_specs.append(spec)
                except Exception:
                    missing_specs.append(spec)
        return missing_specs

    def simulate_install(self, requirements_path: str, python_exe: str, plugin_dir: str, progress_cb: Callable[[float], None] | None = None) -> str:
        """在指定依赖文件上执行 --dry-run 安装，带进度反馈。"""
        if not requirements_path:
            return "[模拟安装] 请先在下拉列表选择要模拟的依赖文件"
        if not os.path.isfile(requirements_path):
            return "[模拟安装] 依赖文件不存在，请重新选择"
        if not self._same_env_root(python_exe, plugin_dir):
            return "[模拟安装] 插件目录与当前 Python 环境根不一致，请切换到对应环境或重新检测"
        
        if progress_cb:
            progress_cb(0.1)
        
        py = python_exe or self._last_python_exe or 'python'
        mirror_url = PYPI_MIRRORS.get(self._last_mirror_name or '', '')
        cmd: List[str] = [py, '-m', 'pip', 'install', '--dry-run', '-r', requirements_path]
        if mirror_url:
            host = mirror_url.split('/')[2]
            cmd += ['--index-url', mirror_url, '--trusted-host', host]
        
        try:
            if progress_cb:
                progress_cb(0.2)
            
            # 先解析依赖文件，获取包数量用于进度估算
            packages = self._parse_dependencies(requirements_path)
            total_packages = len(packages)
            
            if progress_cb:
                progress_cb(0.3)
            
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', timeout=180, creationflags=CREATE_NO_WINDOW)
            out = (proc.stdout or '').strip()
            
            if progress_cb:
                progress_cb(0.8)
            
            if proc.returncode == 0:
                # 分析输出，提供更友好的结果
                lines = out.split('\n')
                install_lines = [line for line in lines if 'Collecting' in line or 'Downloading' in line or 'Installing' in line]
                
                if install_lines:
                    # 提取将要安装的包信息
                    packages_to_install = []
                    for line in install_lines:
                        if 'Collecting' in line:
                            pkg = line.split('Collecting')[1].split()[0]
                            packages_to_install.append(pkg)
                    
                    summary = f"[模拟安装] ✓ 预检通过！{total_packages}个依赖包可以正常安装"
                    if packages_to_install:
                        summary += f"\n[模拟安装] 主要安装包：{', '.join(packages_to_install[:5])}"
                        if len(packages_to_install) > 5:
                            summary += f" 等{len(packages_to_install)}个包"
                    
                    return summary + f"\n\n[详细输出]\n{out[:1200]}"
                else:
                    return f"[模拟安装] ✓ 预检通过！所有依赖已满足，无需安装新包\n\n{out[:800]}"
            else:
                # 分析错误类型，提供更具体的建议
                if "No matching distribution found" in out:
                    return f"[模拟安装] ✗ 失败：找不到匹配的包版本\n请检查包名是否正确或尝试其他版本\n\n{out[:800]}"
                elif "conflict" in out.lower():
                    return f"[模拟安装] ✗ 失败：依赖冲突\n建议先解决冲突或更新相关包\n\n{out[:800]}"
                elif "Permission denied" in out:
                    return f"[模拟安装] ✗ 失败：权限不足\n请检查Python环境权限\n\n{out[:400]}"
                else:
                    return f"[模拟安装] ✗ 失败（返回码{proc.returncode}）\n建议检查依赖冲突或编译环境\n\n{out[:800]}"
                    
        except subprocess.TimeoutExpired:
            return "[模拟安装] ⏰ 超时！依赖项可能过多或网络较慢，建议分批安装"
        except Exception as e:
            return f"[模拟安装] ❌ 执行异常: {e}\n请检查Python环境和网络连接"

    def simulate_install_missing(self, specs: List[str], python_exe: str, mirror_name: str | None = None) -> str:
        """仅对传入的未安装依赖执行 --dry-run 安装。
        specs: 直接传入的依赖规格列表（例如 'numpy==1.26.4' 或 'numpy'）。
        """
        specs = list(specs or [])
        if not specs:
            return '[模拟安装] 未发现可模拟安装的依赖项（列表为空）'
        py = python_exe or self._last_python_exe or 'python'
        mname = mirror_name or self._last_mirror_name or ''
        mirror_url = PYPI_MIRRORS.get(mname, '')
        cmd: List[str] = [py, '-m', 'pip', 'install', '--dry-run'] + specs
        if mirror_url:
            host = mirror_url.split('/')[2]
            cmd += ['-i', mirror_url, '--trusted-host', host]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', timeout=180, creationflags=CREATE_NO_WINDOW)
            out = (proc.stdout or '').strip()
            if proc.returncode == 0:
                return f"[模拟安装] 仅针对未安装依赖成功\n\n{out[:1800]}"
            else:
                return f"[模拟安装] 失败（返回码{proc.returncode}）\n\n{out[:1800]}"
        except subprocess.TimeoutExpired:
            return "[模拟安装] 超时，依赖项可能过多或网络较慢"
        except Exception as e:
            return f"[模拟安装] 执行异常: {e}"

    def view_current_env(self, python_exe: str) -> str:
        """列出当前环境的包（freeze格式），并记录解释器路径以便后续调用。"""
        try:
            self._last_python_exe = python_exe or self._last_python_exe
            py = python_exe or 'python'
            cmd = [py, '-m', 'pip', 'list', '--format=freeze']
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=30, creationflags=CREATE_NO_WINDOW)
            out = proc.stdout or proc.stderr or ''
            return out[:3000]
        except subprocess.TimeoutExpired:
            return "查看当前环境超时"
        except Exception as e:
            return f"查看当前环境失败: {e}"

    def actual_install(self, requirements_path: str, python_exe: str, plugin_dir: str, mirror_name: str, progress_cb: Callable[[float], None] | None = None) -> str:
        """执行 pip install -r <file> 进行实际安装，带进度反馈。"""
        if not requirements_path:
            return "[实际安装] 请先在下拉列表选择要安装的依赖文件"
        if not os.path.isfile(requirements_path):
            return "[实际安装] 依赖文件不存在，请重新选择"
        if not self._same_env_root(python_exe, plugin_dir):
            return "[实际安装] 插件目录与当前 Python 环境根不一致，请切换到对应环境或重新检测"
        
        if progress_cb:
            progress_cb(0.1)
        
        self._last_python_exe = python_exe or self._last_python_exe
        self._last_mirror_name = mirror_name or self._last_mirror_name
        py = python_exe or 'python'
        mirror_url = PYPI_MIRRORS.get(mirror_name or '', '')
        
        # 先解析依赖文件，获取包信息
        packages = self._parse_dependencies(requirements_path)
        total_packages = len(packages)
        
        if progress_cb:
            progress_cb(0.2)
        
        cmd: List[str] = [py, '-m', 'pip', 'install', '-r', requirements_path]
        if mirror_url:
            host = mirror_url.split('/')[2]
            cmd += ['-i', mirror_url, '--trusted-host', host]
        
        try:
            if progress_cb:
                progress_cb(0.3)
            
            # 使用实时输出捕获，提供更好的进度反馈
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            
            output_lines = []
            collected_packages = []
            downloaded_packages = []
            installed_packages = []
            
            while True:
                try:
                    line = proc.stdout.readline()
                except UnicodeDecodeError:
                    # 如果遇到编码错误，尝试跳过这一行
                    continue
                if not line and proc.poll() is not None:
                    break
                if line:
                    msg = line.strip()
                    output_lines.append(msg)
                    try:
                        if msg:
                            self.log(f"[实际安装] {msg}")
                    except Exception:
                        pass
                    # 解析进度信息
                    if 'Collecting' in msg:
                        pkg = msg.split('Collecting')[1].split()[0]
                        collected_packages.append(pkg)
                        if progress_cb and total_packages > 0:
                            progress = 0.3 + 0.4 * len(collected_packages) / total_packages
                            progress_cb(min(progress, 0.7))
                    elif 'Downloading' in msg:
                        pkg = msg.split('Downloading')[1].split('-')[0]
                        downloaded_packages.append(pkg)
                        if progress_cb:
                            progress = 0.7 + 0.2 * len(downloaded_packages) / total_packages
                            progress_cb(min(progress, 0.9))
                    elif 'Successfully installed' in msg:
                        success_part = msg.split('Successfully installed')[1].strip()
                        installed_packages.extend([pkg.strip() for pkg in success_part.split()])
                        if progress_cb:
                            progress_cb(0.95)
            
            returncode = proc.poll()
            full_output = '\n'.join(output_lines)
            
            if progress_cb:
                progress_cb(1.0)
            
            if returncode == 0:
                # 分析安装结果
                if installed_packages:
                    summary = f"[实际安装] ✅ 安装成功！共安装 {len(installed_packages)} 个包"
                    if len(installed_packages) <= 10:
                        summary += f"\n[实际安装] 已安装：{', '.join(installed_packages)}"
                    else:
                        summary += f"\n[实际安装] 主要包：{', '.join(installed_packages[:8])} 等{len(installed_packages)}个"
                    
                    # 检查是否有网络相关的输出
                    if any('Downloaded' in line or 'Cached' in line for line in output_lines):
                        cached_count = len([line for line in output_lines if 'Cached' in line])
                        downloaded_count = len([line for line in output_lines if 'Downloaded' in line])
                        if cached_count > 0 or downloaded_count > 0:
                            summary += f"\n[实际安装] 缓存使用：{cached_count}个，新下载：{downloaded_count}个"
                    
                    return summary + f"\n\n[详细输出]\n{full_output[-800:]}"
                else:
                    return f"[实际安装] ✅ 安装完成！所有依赖已满足，无需新安装\n\n{full_output[-400:]}"
            else:
                # 分析错误类型，提供更具体的建议
                if "No matching distribution found" in full_output:
                    return f"[实际安装] ❌ 失败：找不到匹配的包版本\n建议：检查包名拼写或尝试其他版本\n\n{full_output[-600:]}"
                elif "conflict" in full_output.lower():
                    return f"[实际安装] ❌ 失败：依赖冲突\n建议：先使用'模拟安装'检查冲突，或手动解决依赖\n\n{full_output[-600:]}"
                elif "Permission denied" in full_output:
                    return f"[实际安装] ❌ 失败：权限不足\n建议：以管理员身份运行或检查Python环境权限\n\n{full_output[-400:]}"
                elif "MemoryError" in full_output or "memory" in full_output.lower():
                    return f"[实际安装] ❌ 失败：内存不足\n建议：关闭其他程序或分批安装大型包\n\n{full_output[-400:]}"
                else:
                    return f"[实际安装] ❌ 失败（返回码{returncode}）\n建议：检查网络连接或使用'模拟安装'预检\n\n{full_output[-600:]}"
                    
        except subprocess.TimeoutExpired:
            return "[实际安装] ⏰ 超时！依赖项可能过多或网络较慢\n建议：分批安装或检查网络连接"
        except Exception as e:
            return f"[实际安装] ❌ 执行异常: {e}\n建议：检查Python环境路径和网络连接"

    def actual_install_missing(self, specs: List[str], python_exe: str, mirror_name: str, progress_cb: Callable[[float], None] | None = None) -> str:
        """仅安装传入的未安装依赖规格，逐项输出并推进进度。"""
        specs = list(specs or [])
        if not specs:
            return "[实际安装] 未发现未安装的依赖项"
        py = python_exe or self._last_python_exe or 'python'
        self._last_python_exe = py
        self._last_mirror_name = mirror_name or self._last_mirror_name
        url = PYPI_MIRRORS.get(mirror_name or '', '')
        total = len(specs)
        success_count = 0
        failed: List[str] = []
        for i, spec in enumerate(specs):
            try:
                self.log(f"[实际安装] 安装 {spec} ({i+1}/{total})")
            except Exception:
                pass
            cmd: List[str] = [py, '-m', 'pip', 'install', spec, '--no-deps']
            if url:
                host = url.split('/')[2]
                cmd += ['--index-url', url, '--trusted-host', host, '--extra-index-url', 'https://pypi.org/simple', '--trusted-host', 'pypi.org']
            try:
                r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', timeout=600, creationflags=CREATE_NO_WINDOW)
                out = (r.stdout or '').strip()
                if r.returncode == 0:
                    success_count += 1
                    try:
                        self.log(f"[实际安装] ✅ 成功 {spec}")
                    except Exception:
                        pass
                else:
                    failed.append(spec)
                    # 输出最后几行错误摘要
                    tail = '\n'.join(out.splitlines()[-6:])
                    try:
                        self.log(tail)
                    except Exception:
                        pass
                if progress_cb:
                    progress_cb(min(0.99, 0.1 + 0.8 * (i + 1) / max(1, total)))
            except subprocess.TimeoutExpired:
                failed.append(spec)
                try:
                    self.log(f"[实际安装] ⏰ 超时 {spec}")
                except Exception:
                    pass
            except Exception as e:
                failed.append(spec)
                try:
                    self.log(f"[实际安装] ❌ 出错 {spec} - {e}")
                except Exception:
                    pass
        summary = f"[实际安装] 完成：成功 {success_count} / 失败 {len(failed)}"
        if failed:
            summary += "\n失败列表:\n" + "\n".join(failed[:100])
        return summary

    def compare_environment(self) -> str:
        """生成当前环境快照文件（pip freeze），返回保存路径以便前端后续比较。"""
        py = self._last_python_exe or 'python'
        try:
            proc = subprocess.run([py, '-m', 'pip', 'freeze'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=60, creationflags=CREATE_NO_WINDOW)
            content = (proc.stdout or '').strip()
            ts = int(time.time())
            out_path = os.path.join(os.getcwd(), f'env_snapshot_{ts}.txt')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"[环境比较] 已生成环境快照：{out_path}\n条目数：{max(0, content.count('\n'))}"
        except Exception as e:
            return f"[环境比较] 生成快照失败: {e}"

    def export_environment(self, python_exe: str | None = None, out_path: str | None = None) -> str:
        """导出当前(或指定)Python环境的freeze快照到文件。
        - 若未提供 out_path，则默认写到当前目录 env_snapshot_<ts>.txt。
        返回保存文件路径与条目统计。
        """
        py = python_exe or self._last_python_exe or 'python'
        try:
            proc = subprocess.run([py, '-m', 'pip', 'freeze'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=60, creationflags=CREATE_NO_WINDOW)
            content = (proc.stdout or '').strip()
            ts = int(time.time())
            target = out_path or os.path.join(os.getcwd(), f'env_snapshot_{ts}.txt')
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"[环境导出] 已保存到：{target}\n条目数：{max(0, content.count('\n'))}"
        except subprocess.TimeoutExpired:
            return "[环境导出] 超时"
        except Exception as e:
            return f"[环境导出] 失败: {e}"

    def find_conflicts(self) -> str:
        """运行 pip check 输出冲突信息。"""
        py = self._last_python_exe or 'python'
        try:
            self.log("[冲突检查] 正在检查依赖冲突...")
            proc = subprocess.run([py, '-m', 'pip', 'check'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=60, creationflags=CREATE_NO_WINDOW)
            out = (proc.stdout or proc.stderr or '').strip()
            
            # 英文到中文的翻译映射
            translations = {
                'requires': '需要',
                'which is not installed': '但该包未安装',
                'has requirement': '需要依赖',
                'but you have': '但您已安装',
                'Conflicting requirements': '依赖冲突',
                'No broken requirements found.': '未发现损坏的依赖。'
            }
            
            # 翻译输出内容
            translated_out = out
            for en, zh in translations.items():
                translated_out = translated_out.replace(en, zh)
            
            if proc.returncode == 0:
                return "[冲突检查] ✅ 未发现依赖冲突\n\n所有安装的包依赖关系正常，没有冲突问题。"
            else:
                # 美化输出，添加友好提示
                lines = translated_out.split('\n')
                conflict_count = 0
                friendly_output = "[冲突检查] ❌ 发现依赖冲突\n\n"
                friendly_output += "📋 冲突详情：\n"
                
                for line in lines:
                    if line:
                        conflict_count += 1
                        friendly_output += f"  {conflict_count}. {line}\n"
                
                friendly_output += "\n💡 解决建议：\n"
                friendly_output += "  1. 根据冲突信息，如果不可调和，可以尝试卸载不重要的冲突的包\n"
                friendly_output += "  2. 使用精确查找库名称，升级、降级冲突包的版本号达到两者间的一个平衡点\n"
                
                return friendly_output[:2000]  # 限制输出长度，避免过长
        except subprocess.TimeoutExpired:
            return "[冲突检查] ⏱️  检查超时\n\n依赖冲突检查耗时过长，请稍后重试或手动检查。"
        except Exception as e:
            return f"[冲突检查] 🚫 执行异常: {e}\n\n检查依赖冲突时发生错误，请确保Python环境配置正确。"

    # 与旧前端命名保持兼容的别名
    def find_conflicting_libraries(self) -> str:
        return self.find_conflicts()

    def migrate_environment(self) -> str:
        """列出过期包，提供升级建议（不直接升级）。"""
        py = self._last_python_exe or 'python'
        try:
            proc = subprocess.run([py, '-m', 'pip', 'list', '--outdated'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=60, creationflags=CREATE_NO_WINDOW)
            out = (proc.stdout or '').strip()
            if not out:
                return "[迁移] 未检测到可升级的包"
            return "[迁移] 以下包可升级（使用右侧命令或安装按钮进行升级）：\n\n" + out[:2500]
        except subprocess.TimeoutExpired:
            return "[迁移] 检测超时"
        except Exception as e:
            return f"[迁移] 执行异常: {e}"

    def plan_migration_from_snapshot(self, snapshot_path: str, python_exe: str | None = None) -> str:
        """根据快照文件生成迁移计划：列出需安装/变更的包。
        快照文件通常为 pip freeze 输出（name==version 或其他规格）。"""
        if not snapshot_path or not os.path.isfile(snapshot_path):
            return "[迁移] 快照文件无效"
        py = python_exe or self._last_python_exe or 'python'

        def parse_freeze(path: str) -> Dict[str, str]:
            pkgs: Dict[str, str] = {}
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        s = line.strip()
                        if not s or s.startswith('#'):
                            continue
                        m = re.match(r'^([A-Za-z0-9_.\-]+)==([^\s]+)$', s)
                        if m:
                            pkgs[m.group(1)] = m.group(2)
                        else:
                            n = re.match(r'^([A-Za-z0-9_.\-]+)\b', s)
                            if n:
                                pkgs[n.group(1)] = s
            except Exception:
                pass
            return pkgs

        try:
            # 当前环境 freeze
            cur = subprocess.run([py, '-m', 'pip', 'freeze'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=60, creationflags=CREATE_NO_WINDOW)
            cur_txt = (cur.stdout or '').strip()
            # 写入当前临时文件用于对比（不落盘也可，直接解析）
            current_pkgs: Dict[str, str] = {}
            for line in cur_txt.splitlines():
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                m = re.match(r'^([A-Za-z0-9_.\-]+)==([^\s]+)$', s)
                if m:
                    current_pkgs[m.group(1)] = m.group(2)
                else:
                    n = re.match(r'^([A-Za-z0-9_.\-]+)\b', s)
                    if n:
                        current_pkgs[n.group(1)] = s

            target_pkgs = parse_freeze(snapshot_path)

            ops_install: List[str] = []
            ops_change: List[str] = []
            for name, spec in target_pkgs.items():
                cur_spec = current_pkgs.get(name)
                if cur_spec is None:
                    ops_install.append(f"{name} -> {spec}")
                elif cur_spec != spec:
                    ops_change.append(f"{name}: 当前={cur_spec}  目标={spec}")

            lines: List[str] = []
            lines.append(f"[迁移计划] 快照: {os.path.basename(snapshot_path)}")
            lines.append(f"需安装: {len(ops_install)} 项")
            lines.extend([f"  - {s}" for s in ops_install[:200]])
            lines.append(f"需变更版本: {len(ops_change)} 项")
            lines.extend([f"  - {s}" for s in ops_change[:200]])
            if not ops_install and not ops_change:
                lines.append("环境已与快照一致，无需迁移")
            return "\n".join(lines)
        except subprocess.TimeoutExpired:
            return "[迁移计划] 超时"
        except Exception as e:
            return f"[迁移计划] 生成失败: {e}"

    def apply_migration_from_snapshot(self, snapshot_path: str, python_exe: str | None = None, mirror_name: str | None = None) -> str:
        """根据快照文件执行迁移：通过 pip install -r <snapshot> 安装指定版本。
        注意：该操作不会卸载额外包，仅使已安装包版本与快照匹配。"""
        if not snapshot_path or not os.path.isfile(snapshot_path):
            return "[迁移] 快照文件无效"
        py = python_exe or self._last_python_exe or 'python'
        args = [py, '-m', 'pip', 'install', '-r', snapshot_path]
        if mirror_name:
            url = PYPI_MIRRORS.get(mirror_name)
            if url:
                args.extend(['-i', url])
        try:
            proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=600, creationflags=CREATE_NO_WINDOW)
            out = (proc.stdout or proc.stderr or '').strip()
            if proc.returncode == 0:
                return "[迁移] 已根据快照安装/同步依赖。\n\n" + out[:2500]
            else:
                return f"[迁移] 安装返回码{proc.returncode}\n\n{out[:2500]}"
        except subprocess.TimeoutExpired:
            return "[迁移] 安装超时"
        except Exception as e:
            return f"[迁移] 安装失败: {e}"

    def compare_environment_files(self, file_a: str, file_b: str) -> str:
        """比较两个freeze文件，显示差异：仅在A/仅在B/版本差异。"""
        def parse_freeze(path: str) -> Dict[str, str]:
            pkgs: Dict[str, str] = {}
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        s = line.strip()
                        if not s or s.startswith('#'):
                            continue
                        # 支持常见格式：name==version；对其他格式保留原样
                        m = re.match(r'^([A-Za-z0-9_.\-]+)==([^\s]+)$', s)
                        if m:
                            pkgs[m.group(1)] = m.group(2)
                        else:
                            # 兼容诸如 name @ git+... 或其他规格
                            n = re.match(r'^([A-Za-z0-9_.\-]+)\b', s)
                            if n:
                                pkgs[n.group(1)] = s
            except Exception:
                pass
            return pkgs

        if not file_a or not os.path.isfile(file_a):
            return "[环境比较] 文件A无效"
        if not file_b or not os.path.isfile(file_b):
            return "[环境比较] 文件B无效"

        a = parse_freeze(file_a)
        b = parse_freeze(file_b)
        keys_a = set(a.keys())
        keys_b = set(b.keys())
        only_a = sorted(list(keys_a - keys_b))
        only_b = sorted(list(keys_b - keys_a))
        both = sorted(list(keys_a & keys_b))
        version_diff = []
        for k in both:
            va, vb = a.get(k,''), b.get(k,'')
            if va != vb:
                version_diff.append((k, va, vb))

        lines: List[str] = []
        lines.append(f"[环境比较] A: {os.path.basename(file_a)}  B: {os.path.basename(file_b)}")
        lines.append(f"A仅有: {len(only_a)} 项")
        if only_a:
            lines.extend([f"  - {n}" for n in only_a[:200]])
        lines.append(f"B仅有: {len(only_b)} 项")
        if only_b:
            lines.extend([f"  - {n}" for n in only_b[:200]])
        lines.append(f"版本差异: {len(version_diff)} 项")
        for name, va, vb in version_diff[:200]:
            lines.append(f"  - {name}: A={va}  B={vb}")
        return '\n'.join(lines)

    # ---------------------- 第三方库管理 ----------------------
    def search_library_exact(self, name: str) -> str:
        """检查是否已安装并获取可用版本列表。"""
        if not name:
            return "请输入库名"
        py = self._last_python_exe or 'python'
        msgs: List[str] = []
        # 已安装版本
        try:
            r = subprocess.run([py, '-m', 'pip', 'show', name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0 and r.stdout:
                for line in r.stdout.splitlines():
                    if line.startswith('Version: '):
                        msgs.append(f"当前环境已安装：{name}=={line.split('Version: ',1)[1]}")
                        break
            else:
                msgs.append(f"当前环境未安装：{name}")
        except Exception:
            msgs.append(f"无法检测安装状态：{name}")
        # 版本列表
        mirror_url = PYPI_MIRRORS.get(self._last_mirror_name or '', '')
        
        # 首先尝试使用 pip index versions（推荐方法）
        try:
            cmd = [py, '-m', 'pip', 'index', 'versions', name]
            if mirror_url:
                host = mirror_url.split('/')[2]
                cmd += ['--index-url', mirror_url, '--trusted-host', host]
            
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=15, creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0 and r.stdout:
                lines = r.stdout.splitlines()
                # 查找包含版本信息的行
                for line in lines:
                    if 'Available versions:' in line:
                        # 提取版本列表
                        version_part = line.split('Available versions:', 1)[1].strip()
                        # 清理版本字符串并分割
                        version_part = version_part.replace('(', '').replace(')', '').strip()
                        if version_part:
                            versions = [v.strip() for v in version_part.split(',') if v.strip()]
                            if versions:
                                # 显示所有版本
                                display_versions = versions
                                msgs.append("可用版本：" + ', '.join(display_versions))
                                break
                else:
                    # 如果没有找到标准格式，检查是否有其他版本信息
                    for line in lines:
                        if name.lower() in line.lower() and any(char.isdigit() for char in line):
                            msgs.append("版本信息：" + line.strip())
                            break
                    else:
                        msgs.append("未找到版本信息")
            else:
                # pip index 失败，尝试替代方法
                raise Exception("pip index 不可用")
        except Exception as e:
            # 替代方法：使用 pip install --dry-run 触发错误来获取版本信息
            try:
                alt = subprocess.run([py, '-m', 'pip', 'install', f'{name}==*', '--dry-run'], 
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                                   text=True, errors='replace', timeout=15, creationflags=CREATE_NO_WINDOW)
                err = alt.stderr or ''
                stdout = alt.stdout or ''
                
                # 尝试从错误信息中提取版本
                versions_found = []
                if 'versions:' in err.lower():
                    # 查找版本信息行
                    for line in err.split('\n'):
                        if 'versions:' in line.lower():
                            version_part = line.split('versions:', 1)[1].strip()
                            # 提取版本号（简单的数字和点模式）
                            import re
                            version_matches = re.findall(r'\d+\.\d+(?:\.\d+)?(?:[a-zA-Z]\d*)?', version_part)
                            if version_matches:
                                versions_found = version_matches[:10]  # 限制数量
                                break
                
                # 如果stderr中没有找到，检查stdout
                if not versions_found and stdout:
                    import re
                    # 尝试从stdout中找版本信息
                    version_matches = re.findall(r'\d+\.\d+(?:\.\d+)?(?:[a-zA-Z]\d*)?', stdout)
                    if version_matches:
                        versions_found = list(set(version_matches))[:10]  # 去重并限制数量
                
                if versions_found:
                    msgs.append("可用版本(备选)：" + ', '.join(versions_found))
                else:
                    msgs.append("版本信息：无法获取可用版本列表")
                    
            except Exception as alt_e:
                msgs.append(f"获取版本异常：{alt_e}")
        
        return '\n'.join(msgs)

    def search_library_fuzzy(self, name: str) -> str:
        """在本地环境包列表中进行模糊匹配。"""
        if not name:
            return "请输入搜索关键字"
        py = self._last_python_exe or 'python'
        try:
            r = subprocess.run([py, '-m', 'pip', 'list', '--format=columns'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                return "本地包列表获取失败"
            lines = (r.stdout or '').strip().splitlines()
            matches = []
            for line in lines:
                if name.lower() in line.lower():
                    matches.append(line)
            if not matches:
                return f"未找到匹配：{name}"
            return "本地匹配结果：\n" + '\n'.join(matches[:200])
        except Exception as e:
            return f"本地查找异常: {e}"

    # 与前端方法名保持一致的兼容封装
    def search_library_local(self, name: str) -> str:
        return self.search_library_fuzzy(name)

    def install_whl_file(self, whl_path: str, python_exe: str) -> str:
        return self.install_whl(whl_path, python_exe)

    def install_source_code(self, src_path: str, python_exe: str, mirror_name: str) -> str:
        return self.install_from_source(src_path, python_exe, mirror_name)

    def install_library(self, name: str, version: str, python_exe: str, mirror_name: str) -> str:
        """安装指定库及版本。"""
        if not name:
            return "请输入库名"
        self._last_python_exe = python_exe or self._last_python_exe
        self._last_mirror_name = mirror_name or self._last_mirror_name
        py = python_exe or 'python'
        target = f"{name}=={version}" if version else name
        mirror_url = PYPI_MIRRORS.get(mirror_name or '', '')
        cmd: List[str] = [py, '-m', 'pip', 'install', target]
        if mirror_url:
            host = mirror_url.split('/')[2]
            cmd += ['-i', mirror_url, '--trusted-host', host]
        try:
            # 使用实时输出捕获，提供更好的安装过程反馈
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            
            output_lines = []
            collected_packages = []
            downloaded_packages = []
            installed_packages = []
            
            # 实时读取输出
            while True:
                try:
                    line = proc.stdout.readline()
                except UnicodeDecodeError:
                    # 如果遇到编码错误，尝试跳过这一行
                    continue
                if not line and proc.poll() is not None:
                    break
                if line:
                    msg = line.strip()
                    output_lines.append(msg)
                    try:
                        # 实时输出到日志
                        self.log(f"[库安装] {msg}")
                    except Exception:
                        pass
                    # 解析安装过程信息
                    if 'Collecting' in msg:
                        collected_packages.append(msg)
                    elif 'Downloading' in msg:
                        downloaded_packages.append(msg)
                    elif 'Successfully installed' in msg:
                        success_part = msg.split('Successfully installed')[1].strip()
                        installed_packages.extend([pkg.strip() for pkg in success_part.split()])
            
            returncode = proc.poll()
            full_output = '\n'.join(output_lines)
            
            if returncode == 0:
                # 分析安装结果
                if installed_packages:
                    summary = f"[库安装] ✅ 安装成功！{target}"
                    if len(installed_packages) <= 10:
                        summary += f"\n[库安装] 已安装：{', '.join(installed_packages)}"
                    else:
                        summary += f"\n[库安装] 主要包：{', '.join(installed_packages[:8])} 等{len(installed_packages)}个"
                    
                    # 检查是否有网络相关的输出
                    if any('Downloaded' in line or 'Cached' in line for line in output_lines):
                        cached_count = len([line for line in output_lines if 'Cached' in line])
                        downloaded_count = len([line for line in output_lines if 'Downloaded' in line])
                        if cached_count > 0 or downloaded_count > 0:
                            summary += f"\n[库安装] 缓存使用：{cached_count}个，新下载：{downloaded_count}个"
                    
                    return summary + f"\n\n{full_output}"
                else:
                    return f"[库安装] ✅ 安装完成！{target}\n\n{full_output}"
            else:
                # 分析错误类型，提供更具体的建议
                if "No matching distribution found" in full_output:
                    return f"[库安装] ❌ 失败：找不到匹配的包版本\n建议：检查包名是否正确或尝试其他版本\n\n{full_output}"
                elif "conflict" in full_output.lower():
                    return f"[库安装] ❌ 失败：依赖冲突\n建议：先解决冲突或更新相关包\n\n{full_output}"
                elif "Permission denied" in full_output:
                    return f"[库安装] ❌ 失败：权限不足\n建议：以管理员身份运行或检查Python环境权限\n\n{full_output}"
                elif "MemoryError" in full_output or "memory" in full_output.lower():
                    return f"[库安装] ❌ 失败：内存不足\n建议：关闭其他程序或分批安装大型包\n\n{full_output}"
                else:
                    return f"[库安装] ❌ 失败（返回码{returncode}）：{target}\n建议：检查网络连接或使用'模拟安装'预检\n\n{full_output}"
        except subprocess.TimeoutExpired:
            return f"[库安装] ⏰ 超时！{target} 安装时间过长\n建议：检查网络连接或分批安装"
        except Exception as e:
            return f"[库安装] ❌ 执行异常: {e}\n建议：检查Python环境路径和网络连接"

    def uninstall_library(self, name: str, python_exe: str) -> str:
        """卸载指定库。"""
        if not name:
            return "请输入库名"
        self._last_python_exe = python_exe or self._last_python_exe
        py = python_exe or 'python'
        try:
            proc = subprocess.run([py, '-m', 'pip', 'uninstall', name, '-y'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            out = (proc.stdout or '') + ("\n" + proc.stderr if proc.stderr else '')
            if proc.returncode == 0:
                return f"删除成功：{name}\n\n{out[:1800]}"
            else:
                return f"删除失败（返回码{proc.returncode}）：{name}\n\n{out[:1800]}"
        except Exception as e:
            return f"删除执行异常: {e}"

    def install_whl(self, whl_path: str, python_exe: str) -> str:
        """安装本地whl包文件。"""
        if not whl_path or not os.path.isfile(whl_path):
            return "请选择有效的whl文件"
        self._last_python_exe = python_exe or self._last_python_exe
        py = python_exe or 'python'
        whl_name = os.path.basename(whl_path)
        try:
            # 使用实时输出捕获，提供更好的安装过程反馈
            proc = subprocess.Popen([py, '-m', 'pip', 'install', whl_path], 
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                  text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            
            output_lines = []
            
            # 实时读取输出
            while True:
                try:
                    line = proc.stdout.readline()
                except UnicodeDecodeError:
                    # 如果遇到编码错误，尝试跳过这一行
                    continue
                if not line and proc.poll() is not None:
                    break
                if line:
                    msg = line.strip()
                    output_lines.append(msg)
                    try:
                        # 实时输出到日志
                        self.log(f"[whl安装] {msg}")
                    except Exception:
                        pass
            
            returncode = proc.poll()
            full_output = '\n'.join(output_lines)
            
            if returncode == 0:
                return f"[whl安装] ✅ 安装成功！{whl_name}\n\n{full_output}"
            else:
                return f"[whl安装] ❌ 失败（返回码{returncode}）：{whl_name}\n\n{full_output}"
        except subprocess.TimeoutExpired:
            return f"[whl安装] ⏰ 超时！{whl_name} 安装时间过长\n建议：检查网络连接"
        except Exception as e:
            return f"[whl安装] ❌ 执行异常: {e}\n建议：检查Python环境路径"

    def install_from_source(self, src_path: str, python_exe: str, mirror_name: str) -> str:
        """从源码压缩包或源码目录安装。"""
        if not src_path or not os.path.exists(src_path):
            return "请选择有效的源码路径"
        self._last_python_exe = python_exe or self._last_python_exe
        self._last_mirror_name = mirror_name or self._last_mirror_name
        py = python_exe or 'python'
        mirror_url = PYPI_MIRRORS.get(mirror_name or '', '')
        cmd: List[str] = [py, '-m', 'pip', 'install', src_path]
        if mirror_url:
            host = mirror_url.split('/')[2]
            cmd += ['-i', mirror_url, '--trusted-host', host]
        src_name = os.path.basename(src_path)
        try:
            # 使用实时输出捕获，提供更好的安装过程反馈
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            
            output_lines = []
            
            # 实时读取输出
            while True:
                try:
                    line = proc.stdout.readline()
                except UnicodeDecodeError:
                    # 如果遇到编码错误，尝试跳过这一行
                    continue
                if not line and proc.poll() is not None:
                    break
                if line:
                    msg = line.strip()
                    output_lines.append(msg)
                    try:
                        # 实时输出到日志
                        self.log(f"[源码安装] {msg}")
                    except Exception:
                        pass
            
            returncode = proc.poll()
            full_output = '\n'.join(output_lines)
            
            if returncode == 0:
                return f"[源码安装] ✅ 安装成功！{src_name}\n\n{full_output}"
            else:
                return f"[源码安装] ❌ 失败（返回码{returncode}）：{src_name}\n\n{full_output}"
        except subprocess.TimeoutExpired:
            return f"[源码安装] ⏰ 超时！{src_name} 安装时间过长\n建议：检查网络连接"
        except Exception as e:
            return f"[源码安装] ❌ 执行异常: {e}\n建议：检查Python环境路径和网络连接"

    # ---------------------- CMD 执行 ----------------------
    def execute_command(self, cmd: str) -> str:
        try:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            out, err = proc.communicate(timeout=60)
            return out or err or "(无输出)"
        except Exception as e:
            return f"命令执行失败: {e}"

    def pip_params_help(self) -> str:
        return (
            "【标准用法】\n"
            "- 使用当前环境: python -m pip <子命令> [参数]\n"
            "- 指定环境: C\\Path\\To\\Python\\python.exe -m pip <子命令> [参数]\n\n"
            "【安装/升级/卸载】\n"
            "- 安装指定版本: python -m pip install package==1.2.3\n"
            "- 安装最新版本: python -m pip install package\n"
            "- 多包安装: python -m pip install packageA==x packageB==y\n"
            "- 升级已安装包: python -m pip install --upgrade package\n"
            "- 卸载包: python -m pip uninstall package\n"
            "- 从依赖文件安装: python -m pip install -r requirements.txt\n"
            "- 强制重装: python -m pip install --force-reinstall package==1.2.3\n\n"
            "【独立/不安装依赖/仅下载】\n"
            "- 独立安装(不安装依赖): python -m pip install package --no-deps\n"
            "- 忽略已安装重新安装: python -m pip install --ignore-installed -r requirements.txt\n"
            "- 仅下载不安装: python -m pip download package==1.2.3\n\n"
            "【查询与状态】\n"
            "- 列出包(友好): python -m pip list\n"
            "- 列出包(JSON): python -m pip list --format=json\n"
            "- 显示包详情: python -m pip show package\n"
            "- 检查依赖冲突: python -m pip check\n"
            "- 查看可用版本: python -m pip index versions package\n\n"
            "【环境快照与同步】\n"
            "- 导出快照: python -m pip freeze > env_snapshot.txt\n"
            "- 从快照安装: python -m pip install -r env_snapshot.txt\n\n"
            "【源与镜像】\n"
            "- 指定镜像源: python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ package\n"
            "- 可信主机: --trusted-host mirrors.aliyun.com\n"
            "- 主索引+备用: python -m pip install --index-url https://mirrors.aliyun.com/pypi/simple/ --extra-index-url https://pypi.org/simple --trusted-host mirrors.aliyun.com --trusted-host pypi.org package\n\n"
            "【构建与本地安装】\n"
            "- 安装轮子: python -m pip install dist\\package-1.2.3-cp310-win_amd64.whl\n"
            "- 源码目录安装: python -m pip install .\n"
            "- 压缩包安装: python -m pip install path\\to\\package-1.2.3.tar.gz\n\n"
            "【构建/打包】\n"
            "- 构建轮子: python -m pip wheel -r requirements.txt -w dist\n"
            "- 构建单包: python -m pip wheel package==1.2.3\n\n"
            "【安装目标与范围】\n"
            "- 安装到目标目录: python -m pip install package --target D:\\libs\n"
            "- 安装到用户目录: python -m pip install package --user\n\n"
            "【二进制/源码选择】\n"
            "- 仅二进制: python -m pip install package --only-binary :all:\n"
            "- 强制源码: python -m pip install package --no-binary :all:\n\n"
            "【缓存与诊断】\n"
            "- 缓存信息: python -m pip cache info\n"
            "- 列出缓存: python -m pip cache list\n"
            "- 清理缓存: python -m pip cache purge\n"
            "- 干跑预检: python -m pip install --dry-run package\n\n"
            "【编译隔离与预发布】\n"
            "- 关闭构建隔离: python -m pip install package --no-build-isolation\n"
            "- 包含预发布版本: python -m pip install package --pre\n\n"
            "【约束文件】\n"
            "- 使用约束文件: python -m pip install -r requirements.txt -c constraints.txt\n\n"
            "【代理与网络】\n"
            "- 使用代理: python -m pip install package --proxy http://user:pass@host:port\n"
            "- 超时/重试: --timeout 60 --retries 3\n"
            "- 禁用版本检查: python -m pip --disable-pip-version-check list\n\n"
            "【配置持久化】\n"
            "- 设置默认镜像: python -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/\n"
            "- 添加可信主机: python -m pip config set global.trusted-host mirrors.aliyun.com\n"
            "- 查看配置: python -m pip config list\n\n"
            "【技巧】\n"
            "- 包名规范化: importlib-metadata 安装后可能写作 importlib_metadata\n"
            "- 指定平台版本: 例如 torch==2.1.0+cu118\n"
        )

    def _same_env_root(self, py_exe: str, plugin_dir: str) -> bool:
        """对比 Python 解释器与插件目录是否在同一根环境（盘符+一级目录）"""
        if not py_exe or not plugin_dir:
            return False
        try:
            d_py, p_py = os.path.splitdrive(os.path.abspath(py_exe))
            d_pl, p_pl = os.path.splitdrive(os.path.abspath(plugin_dir))
            if d_py.lower() != d_pl.lower():
                return False
            parts_py = [x for x in p_py.replace('\\','/').split('/') if x]
            parts_pl = [x for x in p_pl.replace('\\','/').split('/') if x]
            return len(parts_py) >= 1 and len(parts_pl) >= 1 and parts_py[0].lower() == parts_pl[0].lower()
        except Exception:
            return False
    def scan_customnodes_dependencies(self, dir_path: str, python_exe: str, cache_list: List[str],
                                    progress_cb: Callable[[float], None] | None = None) -> Dict[str, List[str] | str]:
        """
        仅扫描根目录和一级子目录中的 requirements.txt 文件，并判断依赖是否已安装。
        - 只检查根目录和第一层子目录中的 requirements.txt
        - 跳过 cache_list 中已确认全部安装的文件
        - progress_cb(0~1) 可选，用于实时反馈进度
        - 返回 missing_files（未安装的依赖文件路径列表）、all_ok_files（已安装的依赖文件路径列表）、
          missing_packages（未安装的包名列表）、message（汇总信息）
        """
        missing_files: List[str] = []
        all_ok_files: List[str] = []
        missing_packages: List[str] = []  # 新增：收集所有未安装的包名
        cache_set = set(cache_list or [])
        if not dir_path or not os.path.isdir(dir_path):
            return {"missing_files": [], "all_ok_files": [], "message": "[插件维护] 目录不存在或不可访问"}

        py = python_exe or "python"

        candidates: List[str] = []
        try:
            # 根目录：仅 requirements.txt
            root_files = set(os.listdir(dir_path))
            if "requirements.txt" in root_files:
                candidates.append(os.path.join(dir_path, "requirements.txt"))

            # 一级子目录中的依赖文件（扫描所有第一层子目录）
            for name in root_files:
                sub = os.path.join(dir_path, name)
                if os.path.isdir(sub):
                    try:
                        # 只检查第一层子目录中的 requirements.txt
                        req_file = os.path.join(sub, "requirements.txt")
                        if os.path.exists(req_file):
                            candidates.append(req_file)
                    except Exception:
                        pass
        except Exception:
            pass

        # 去重
        unique_candidates = []
        seen = set()
        for p in candidates:
            if p not in seen:
                seen.add(p)
                unique_candidates.append(p)

        # 优化：批量获取已安装包，避免对每个文件都重复查询
        total = len(unique_candidates)
        if total == 0:
            return {"missing_files": [], "all_ok_files": [], "message": "[插件维护] 未找到依赖文件"}
        
        # 获取已安装包集合（一次性操作）
        if progress_cb:
            progress_cb(0.1)
        
        installed_packages = self._get_installed_packages_batch(py, progress_cb)
        
        # 批量处理所有依赖文件
        for idx, req_file in enumerate(unique_candidates):
            if progress_cb:
                # 实时更新进度
                progress_cb(0.1 + 0.8 * (idx + 1) / total)
            
            try:
                if req_file in cache_set:
                    all_ok_files.append(req_file)
                    continue
                    
                packages = self._parse_dependencies(req_file)
                if not packages:
                    all_ok_files.append(req_file)
                    continue
                    
                # 使用批量获取的结果进行快速检查
                not_installed = []
                for name in packages:
                    if name.startswith("git+"):
                        continue
                    # 标准化包名进行匹配，处理下划线vs连字符的问题
                    normalized_name = self._normalize_package_name(name)
                    if normalized_name in installed_packages:
                        continue
                    # 回退到 pip show 双形态检查（下划线与连字符），与 check_dependencies 方法保持一致
                    try:
                        hyphen_name = normalized_name
                        underscore_name = name.lower()
                        is_installed = self._is_package_installed(py, hyphen_name) or self._is_package_installed(py, underscore_name)
                    except Exception:
                        is_installed = False
                    if not is_installed:
                        not_installed.append(name)
                
                if not_installed:
                    missing_files.append(req_file)
                    missing_packages.extend(not_installed)  # 收集未安装的包名
                    self.log(f"[依赖缺失] {req_file}: {', '.join(not_installed)}")
                else:
                    all_ok_files.append(req_file)
                    
            except Exception as e:
                missing_files.append(req_file)
                self.log(f"扫描失败 {req_file}: {e}")

        if progress_cb:
            progress_cb(1.0)
            
        msg = f"[插件维护] 扫描完成：已就绪 {len(all_ok_files)} 个，需安装 {len(missing_files)} 个"
        
        # 去重并排序未安装的包名
        unique_missing_packages = sorted(set(missing_packages))
            
        return {"missing_files": missing_files, "all_ok_files": all_ok_files, "missing_packages": unique_missing_packages, "message": msg}

    def git_check_updates(self, plugin_dirs: List[str]) -> Dict[str, object]:
        """
        检查多个插件目录是否有Git更新。
        返回 {updates: List[dict], message: str}
        每个更新dict包含: {path: str, has_update: bool, current_commit: str, latest_commit: str, message: str}
        """
        updates = []
        total = len(plugin_dirs)
        if total == 0:
            return {"updates": [], "message": "没有可检查的插件目录"}
        
        for i, plugin_dir in enumerate(plugin_dirs):
            if not plugin_dir or not os.path.isdir(plugin_dir):
                updates.append({
                    "path": plugin_dir,
                    "has_update": False,
                    "current_commit": "",
                    "latest_commit": "",
                    "message": "目录不存在"
                })
                continue
                
            try:
                # 检查是否是git仓库
                git_dir = os.path.join(plugin_dir, '.git')
                if not os.path.isdir(git_dir):
                    updates.append({
                        "path": plugin_dir,
                        "has_update": False,
                        "current_commit": "",
                        "latest_commit": "",
                        "message": "不是Git仓库"
                    })
                    continue
                
                # 获取当前commit
                result_current = subprocess.run(
                    ["git", "rev-parse", "HEAD"], 
                    cwd=plugin_dir, 
                    capture_output=True, 
                    text=True, 
                    errors='replace',
                    creationflags=CREATE_NO_WINDOW
                )
                current_commit = (result_current.stdout or '').strip() if result_current.returncode == 0 else ""
                
                # 获取远程最新信息
                result_fetch = subprocess.run(
                    ["git", "fetch", "--dry-run"], 
                    cwd=plugin_dir, 
                    capture_output=True, 
                    text=True, 
                    errors='replace',
                    creationflags=CREATE_NO_WINDOW
                )
                
                # 检查是否有更新
                result_status = subprocess.run(
                    ["git", "status", "-uno"], 
                    cwd=plugin_dir, 
                    capture_output=True, 
                    text=True, 
                    errors='replace',
                    creationflags=CREATE_NO_WINDOW
                )
                
                has_update = False
                message = "已是最新"
                
                if result_status.returncode == 0:
                    status_output = result_status.stdout
                    if "Your branch is behind" in status_output:
                        has_update = True
                        message = "有可用更新"
                    elif "Your branch is up to date" in status_output:
                        message = "已是最新"
                    elif "Your branch is ahead" in status_output:
                        message = "本地有未推送的更改"
                
                # 获取最新commit
                result_latest = subprocess.run(
                    ["git", "rev-parse", "@{u}"], 
                    cwd=plugin_dir, 
                    capture_output=True, 
                    text=True, 
                    errors='replace',
                    creationflags=CREATE_NO_WINDOW
                )
                latest_commit = (result_latest.stdout or '').strip() if result_latest.returncode == 0 else ""
                
                updates.append({
                    "path": plugin_dir,
                    "has_update": has_update,
                    "current_commit": current_commit[:8] if current_commit else "",
                    "latest_commit": latest_commit[:8] if latest_commit else "",
                    "message": message
                })
                
            except Exception as e:
                updates.append({
                    "path": plugin_dir,
                    "has_update": False,
                    "current_commit": "",
                    "latest_commit": "",
                    "message": f"检查失败: {str(e)}"
                })
        
        # 统计结果
        total_checked = len([u for u in updates if u["message"] not in ["目录不存在", "不是Git仓库", "检查失败"]])
        has_updates = len([u for u in updates if u["has_update"]])
        
        summary = f"检查完成: {total_checked}个插件中，{has_updates}个有更新"
        return {"updates": updates, "message": summary}

    def git_update_plugin(self, plugin_dir: str) -> Dict[str, object]:
        """
        更新单个插件目录。
        返回 {ok: bool, message: str}
        """
        if not plugin_dir or not os.path.isdir(plugin_dir):
            return {"ok": False, "message": "插件目录不存在"}
        
        try:
            # 检查是否是git仓库
            git_dir = os.path.join(plugin_dir, '.git')
            if not os.path.isdir(git_dir):
                return {"ok": False, "message": "不是Git仓库"}
            
            # 执行git pull
            result = subprocess.run(
                ["git", "pull"], 
                cwd=plugin_dir, 
                capture_output=True, 
                text=True, 
                errors='replace'
            )
            
            if result.returncode == 0:
                return {"ok": True, "message": f"更新成功:\n{result.stdout}"}
            else:
                return {"ok": False, "message": f"更新失败:\n{result.stderr}"}
                
        except Exception as e:
            return {"ok": False, "message": f"更新异常: {str(e)}"}

    def git_clone(self, url: str, dest: str, progress_cb: Optional[Callable[[str], None]] = None) -> Dict[str, object]:
        """克隆Git插件到指定目录，返回 {ok, path, message}。
        progress_cb: 可选的回调函数，用于实时显示克隆进度信息
        """
        if not url:
            return {"ok": False, "path": None, "message": "未提供Git地址"}
        if not dest or not os.path.isdir(dest):
            return {"ok": False, "path": None, "message": "目标目录无效"}
        # 推断仓库目录名
        repo_name = url.rstrip('/').split('/')[-1]
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
        target = os.path.join(dest, repo_name)
        try:
            # 已存在则提示
            if os.path.isdir(target):
                return {"ok": True, "path": target, "message": f"仓库已存在: {target}"}
            cmd = ["git", "clone", url]
            
            # 使用Popen实时获取输出
            proc = subprocess.Popen(cmd, cwd=dest, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                  text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            
            full_output = []
            # 实时读取输出
            while proc.poll() is None:
                if proc.stdout:
                    line = proc.stdout.readline()
                    if line:
                        line = line.strip()
                        full_output.append(line)
                        # 调用回调函数显示进度
                        if progress_cb:
                            progress_cb(f"[克隆] {line}")
            
            # 读取剩余输出
            if proc.stdout:
                remaining = proc.stdout.read()
                if remaining:
                    remaining = remaining.strip()
                    full_output.append(remaining)
                    if progress_cb:
                        progress_cb(f"[克隆] {remaining}")
            
            out = "\n".join(full_output)
            if proc.returncode == 0 and os.path.isdir(target):
                return {"ok": True, "path": target, "message": f"克隆成功到: {target}"}
            return {"ok": False, "path": None, "message": f"克隆失败（返回码{proc.returncode}）:\n{out}"}
        except Exception as e:
            return {"ok": False, "path": None, "message": f"克隆异常: {e}"}

    def find_dependency_file(self, plugin_dir: str) -> Optional[str]:
        """在插件目录中寻找依赖文件，优先返回 requirements.txt，其次 pyproject.toml。"""
        try:
            p1 = os.path.join(plugin_dir, "requirements.txt")
            if os.path.isfile(p1):
                return p1
            # 其他 requirements*.txt
            for fn in os.listdir(plugin_dir):
                if fn.startswith("requirements") and fn.endswith(".txt"):
                    return os.path.join(plugin_dir, fn)
            p2 = os.path.join(plugin_dir, "pyproject.toml")
            if os.path.isfile(p2):
                return p2
        except Exception:
            pass
        return None

    # ---------------------- 辅助方法 ----------------------
    def _get_installed_packages_batch(self, python_exe: str, progress_cb: Optional[Callable[[float], None]] = None) -> set[str]:
        """批量获取已安装的包名，返回小写包名集合以提高性能"""
        current_time = time.time()
        
        # 检查缓存是否有效（30秒内且环境一致）
        if (self._installed_packages_cache is not None and 
            current_time - self._cache_timestamp < self._cache_timeout and
            self._last_python_exe == python_exe):
            if progress_cb:
                progress_cb(0.2)  # 直接使用缓存
            return self._installed_packages_cache
        
        try:
            if progress_cb:
                progress_cb(0.1)
                
            # 使用pip list一次性获取所有已安装的包
            cmd = [python_exe, '-m', 'pip', 'list', '--format=json']
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=30, creationflags=CREATE_NO_WINDOW)
            
            if progress_cb:
                progress_cb(0.15)
                
            if proc.returncode == 0 and proc.stdout:
                import json
                try:
                    packages = json.loads((proc.stdout or '').strip())
                    # 返回小写包名集合，便于快速查找
                    package_names = {pkg.get('name', '').lower() for pkg in packages if pkg.get('name')}
                    
                    # 更新缓存
                    self._installed_packages_cache = package_names
                    self._cache_timestamp = current_time
                    self._last_python_exe = python_exe
                    
                    if progress_cb:
                        progress_cb(0.2)
                        
                    return package_names
                except json.JSONDecodeError:
                    pass
                    
            # 如果JSON格式失败，回退到文本格式
            cmd = [python_exe, '-m', 'pip', 'list']
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=30, creationflags=CREATE_NO_WINDOW)
            
            if proc.returncode == 0 and proc.stdout:
                package_names = set()
                lines = (proc.stdout or '').strip().split('\n')
                # 跳过标题行
                for line in lines[2:] if len(lines) > 2 else lines:
                    parts = line.split()
                    if parts:
                        package_names.add(parts[0].lower())
                
                # 更新缓存
                self._installed_packages_cache = package_names
                self._cache_timestamp = current_time
                self._last_python_exe = python_exe
                
                if progress_cb:
                    progress_cb(0.2)
                    
                return package_names
                
        except Exception as e:
            self.log(f"批量获取已安装包失败: {e}")
            
        if progress_cb:
            progress_cb(0.2)
            
        # 如果批量获取失败，返回空集合，让调用方使用备用方案
        return set()

    def _is_package_installed(self, python_exe: str, name: str) -> bool:
        """通过 pip show 判断包是否已安装。"""
        try:
            cmd = [python_exe, '-m', 'pip', 'show', name] if python_exe else ['pip', 'show', name]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', creationflags=CREATE_NO_WINDOW)
            return proc.returncode == 0
        except Exception:
            return False

    def _parse_dependencies(self, file_path: str) -> List[str]:
        """
        解析依赖文件，返回待检查的包名列表。
        仅处理 requirements*.txt；pyproject.toml 已剔除。
        """
        base = os.path.basename(file_path).lower()
        if base.endswith('.txt'):
            return self._parse_requirements_txt(file_path)
        return []

    def _parse_requirements_txt(self, file_path: str) -> List[str]:
        items: List[str] = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith('#'):
                        continue
                    # 处理分号后的环境标记
                    if ';' in s:
                        s = s.split(';', 1)[0].strip()
                    # git+ 直接保留为特殊项，后续跳过安装检测
                    if s.startswith('git+'):
                        items.append('git+')
                        continue
                    # 提取包名（忽略版本操作符）
                    m = re.match(r'^([A-Za-z0-9_.\-]+)', s)
                    if m:
                        items.append(m.group(1))
        except Exception:
            pass
        return items



    def _normalize_package_name(self, name: str) -> str:
        """标准化包名：将下划线转换为连字符，统一为小写。
        根据PEP 503，包名应该标准化为连字符格式。"""
        if not name:
            return ""
        # 转换为小写并替换下划线为连字符
        return name.lower().replace('_', '-')

    def _extract_name_from_spec(self, spec: str) -> Optional[str]:
        """从 'package==1.2.3' 或 'package>=x' 等规格中提取包名。"""
        if not spec:
            return None
        if spec.startswith('git+'):
            return None
        m = re.match(r'^([A-Za-z0-9_.\-]+)', spec.strip())
        return m.group(1) if m else None