# 导入必要的模块
import sys
import os
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# 项目版本信息
VERSION = "V1.2"

# 尝试导入packaging.version，如果不可用则使用自定义的版本比较函数
try:
    from packaging.version import parse as parse_version
except ImportError:
    # 自定义简单的版本比较函数
    def parse_version(version):
        def normalize(v):
            return [int(x) for x in v.split('.') if x.isdigit()]
        return normalize(version)

# 国内常用的pip镜像源
PYPI_MIRRORS = {
    '官方源': '',
    '阿里云': 'https://mirrors.aliyun.com/pypi/simple/',
    '清华大学': 'https://pypi.tuna.tsinghua.edu.cn/simple/',
    '中国科学技术大学': 'https://pypi.mirrors.ustc.edu.cn/simple/',
    '豆瓣': 'https://pypi.douban.com/simple/',
    '华为云': 'https://mirrors.huaweicloud.com/repository/pypi/simple/'
}

# 判断是否为Windows平台，并且不是在交互式Python环境中运行
if sys.platform.startswith('win') and not hasattr(sys, 'ps1'):
    # 隐藏控制台窗口（更可靠的方法，适用于Python脚本和打包后的exe）
    try:
        import ctypes
        # 获取控制台窗口句柄
        console_handle = ctypes.windll.kernel32.GetConsoleWindow()
        if console_handle != 0:
            # 隐藏控制台窗口
            ctypes.windll.user32.ShowWindow(console_handle, 0)  # SW_HIDE = 0
            # 设置窗口位置到屏幕外（额外保险）
            ctypes.windll.user32.SetWindowPos(console_handle, None, -10000, -10000, 0, 0, 0x0001)  # SWP_NOSIZE
    except Exception as e:
        pass
    
    # 尝试重定向标准输出和错误流到空设备，避免在无控制台模式下出错
    try:
        # 打开空设备
        devnull = open(os.devnull, 'w')
        sys.stdout = devnull
        sys.stderr = devnull
    except:
        try:
            # 如果打开空设备失败，尝试将输出重定向到对象
            class NullWriter:
                def write(self, _): pass
                def flush(self): pass
            sys.stdout = NullWriter()
            sys.stderr = NullWriter()
        except:
            pass
import subprocess
import re
import os
from threading import Thread
import shutil
import time

class EnvironmentCheckerApp:
    def find_python_in_path(self):
        """检测系统PATH变量中是否存在Python环境"""
        python_paths = []
        # 获取系统PATH变量
        path_env = os.environ.get('PATH', '')
        # 分割PATH变量
        paths = path_env.split(os.pathsep)
        
        # 检查每个路径中是否包含Python可执行文件
        for path in paths:
            if path.strip():
                # 检查常见的Python可执行文件名
                for python_exe in ['python.exe', 'python3.exe']:
                    python_path = os.path.join(path, python_exe)
                    if os.path.isfile(python_path) and os.access(python_path, os.X_OK):
                        python_paths.append(python_path)
                        # 只返回第一个找到的有效Python环境
                        return python_path
        
        # 如果没有找到，返回None
        return None
        
    def __init__(self, root):
        self.root = root
        self.root.title(f"Python环境依赖冲突检查器{VERSION} 练老师 QQ群：723799422")
        self.root.geometry("800x600")
        self.root.minsize(600, 500)
        
        # 设置窗口在桌面中央显示
        self.root.update_idletasks()  # 更新窗口大小信息
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        
        # 设置中文字体支持
        self.style = ttk.Style()
        self.style.configure(
            "TButton",
            font=("SimHei", 10)
        )
        self.style.configure(
            "TLabel",
            font=("SimHei", 10)
        )
        
        # 检测系统PATH变量中的Python环境
        system_python = self.find_python_in_path()
        
        # 初始化所有必要的属性
        # 存储requirements.txt路径
        self.requirements_path = ""
        # 存储Python环境路径
        self.python_env_path = ""
        # 存储Python可执行文件路径
        if system_python:
            self.python_exe_path = system_python
            self.python_env_path = os.path.dirname(system_python)
        else:
            # 如果PATH中没有Python环境，设置为未选择状态
            self.python_exe_path = ""
            self.python_env_path = ""
        # 存储依赖包列表
        self.dependencies = []
        # 存储检查结果
        self.check_results = {}
        # 标记是否有冲突
        self.has_conflicts = True
        # 存储当前选择的镜像源
        self.selected_mirror = "官方源"
        # 进度条变量
        self.progress_var = tk.DoubleVar()
        
        # 创建界面（在所有属性初始化后）
        self.create_widgets()
        
    def create_widgets(self):
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 顶部选择文件区域
        top_frame = ttk.Frame(main_frame, padding="5")
        top_frame.pack(fill=tk.X, pady=5)
        
        self.file_label = ttk.Label(top_frame, text="未选择requirements.txt文件")
        self.file_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        select_btn = ttk.Button(top_frame, text="选择文件", command=self.select_requirements_file)
        select_btn.pack(side=tk.RIGHT, padx=5)
        
        # Python环境选择区域
        env_frame = ttk.Frame(main_frame, padding="5")
        env_frame.pack(fill=tk.X, pady=5)
        
        # 根据是否在PATH中找到Python环境显示不同的文本
        if self.python_exe_path:
            env_text = f"当前Python环境: {self.python_exe_path}"
        else:
            env_text = "当前Python环境: 未选择"
            
        self.env_label = ttk.Label(env_frame, text=env_text)
        self.env_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        env_btn = ttk.Button(env_frame, text="选择Python环境", command=self.select_python_environment)
        env_btn.pack(side=tk.RIGHT, padx=5)
        
        # 镜像源选择区域
        mirror_frame = ttk.Frame(main_frame, padding="5")
        mirror_frame.pack(fill=tk.X, pady=5)
        
        mirror_label = ttk.Label(mirror_frame, text="选择PyPI镜像源:")
        mirror_label.pack(side=tk.LEFT, padx=5)
        
        # 创建镜像源下拉框
        self.mirror_var = tk.StringVar(value=self.selected_mirror)
        self.mirror_combobox = ttk.Combobox(mirror_frame, textvariable=self.mirror_var, values=list(PYPI_MIRRORS.keys()), state="readonly", width=20)
        self.mirror_combobox.pack(side=tk.LEFT, padx=5)
        self.mirror_combobox.bind("<<ComboboxSelected>>", self.on_mirror_change)
        
        # 添加镜像源测试按钮
        test_mirror_btn = ttk.Button(mirror_frame, text="测试镜像源", command=self.test_mirror_speed)
        test_mirror_btn.pack(side=tk.LEFT, padx=5)
        
        # 中间按钮区域
        btn_frame = ttk.Frame(main_frame, padding="5")
        btn_frame.pack(fill=tk.X, pady=5)
        
        check_btn = ttk.Button(btn_frame, text="检查环境冲突", command=self.start_checking)
        check_btn.pack(side=tk.LEFT, padx=5)
        
        simulate_btn = ttk.Button(btn_frame, text="模拟安装", command=self.start_simulation)
        simulate_btn.pack(side=tk.LEFT, padx=5)
        
        view_btn = ttk.Button(btn_frame, text="查看当前环境", command=self.view_current_env)
        view_btn.pack(side=tk.LEFT, padx=5)
        
        # 实际安装按钮，初始启用
        self.install_btn = ttk.Button(btn_frame, text="实际安装", command=self.start_installation)
        self.install_btn.pack(side=tk.LEFT, padx=5)
        
        # 清空信息按钮
        clear_btn = ttk.Button(btn_frame, text="清空信息", command=self.clear_results)
        clear_btn.pack(side=tk.LEFT, padx=5)
        
        # 比较环境文件按钮
        compare_btn = ttk.Button(btn_frame, text="比较环境文件", command=self.compare_environment_files)
        compare_btn.pack(side=tk.LEFT, padx=5)

        # 底部结果显示区域
        bottom_frame = ttk.Frame(main_frame, padding="5")
        bottom_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 创建带滚动条的文本框
        self.result_text = tk.Text(bottom_frame, wrap=tk.WORD, font=("SimHei", 10))
        self.result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        scrollbar = ttk.Scrollbar(bottom_frame, command=self.result_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.result_text.config(yscrollcommand=scrollbar.set)
        
        # 创建进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_bar.pack_forget()  # 初始隐藏
        
    def select_requirements_file(self):
        """选择requirements.txt文件"""
        file_path = filedialog.askopenfilename(
            title="选择requirements.txt文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*")]
        )
        
        if file_path:
            self.requirements_path = file_path
            self.file_label.config(text=f"已选择: {os.path.basename(file_path)}")
            
            # 解析requirements.txt
            try:
                self.parse_requirements_file()
                self.update_result_text(f"成功解析requirements.txt文件，共找到 {len(self.dependencies)} 个依赖包。\n\n")
            except Exception as e:
                # 在主线程中显示错误消息
                self.root.after(0, lambda error=str(e): messagebox.showerror("错误", f"解析文件失败: {error}"))
            
            # 重置安装按钮状态
            self.install_btn.config(state=tk.DISABLED)
            self.has_conflicts = True
    
    def parse_requirements_file(self):
        """解析requirements.txt文件，提取依赖包和版本信息"""
        if not os.path.exists(self.requirements_path):
            raise FileNotFoundError("requirements.txt文件不存在")
        
        self.dependencies = []
        
        with open(self.requirements_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            line = line.strip()
            
            # 处理分号注释 - 移除分号后的内容
            if ';' in line:
                line = line.split(';', 1)[0].strip()
            
            # 跳过注释和空行
            if line.startswith('#') or not line:
                continue
            
            # 处理git+URL格式的依赖
            if line.startswith('git+'):
                # 对于git+URL格式，我们无法直接解析包名和版本
                # 但我们可以尝试从URL中提取包名作为参考
                # 这种格式的依赖在实际安装时由pip处理
                package_name = self._extract_package_name_from_git_url(line)
                self.dependencies.append((package_name, 'git+', line))
                continue
            
            # 处理常见格式: package==version, package>=version, package<=version等
            match = re.match(r'^([a-zA-Z0-9_-]+)([<>=!]+)([0-9a-zA-Z.+-]+)', line)
            if match:
                package_name = match.group(1)
                operator = match.group(2)
                version = match.group(3)
                self.dependencies.append((package_name, operator, version))
            else:
                # 如果没有版本限制，只添加包名
                self.dependencies.append((line, '', ''))
                
    def _extract_package_name_from_git_url(self, git_url):
        """从git URL中尝试提取包名"""
        # 简单的处理逻辑：从URL中提取最后一个路径段，并移除.git后缀
        import re
        # 匹配URL中的最后一个路径段（可能包含.git后缀）
        match = re.search(r'/([^/]+?)(?:\.git)?$', git_url)
        if match:
            return match.group(1)
        # 如果无法提取，返回'git_package'作为默认值
        return 'git_package'
    
    def start_checking(self):
        """开始检查环境依赖冲突"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            # 在主线程中显示警告消息
            self.root.after(0, lambda: messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境"))
            return
            
        if not self.dependencies:
            # 在主线程中显示警告消息
            self.root.after(0, lambda: messagebox.showwarning("警告", "请先选择并解析requirements.txt文件"))
            return
        
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text("开始检查环境依赖冲突...\n\n")
        
        # 在新线程中执行检查，避免界面卡顿
        check_thread = Thread(target=self.check_environment)
        check_thread.daemon = True
        check_thread.start()
    
    def check_environment(self):
        """检查当前环境与requirements.txt中的依赖是否冲突"""
        self.check_results = {}  
        total_packages = len(self.dependencies)
        self.has_conflicts = False  # 重置冲突标志
        
        for i, (package_name, operator, version) in enumerate(self.dependencies):
            # 更新进度条
            progress = (i + 1) / total_packages * 100
            
            # 在主线程中更新进度条并刷新UI
            if self.root._windowingsystem is not None and self.root.winfo_exists():
                self.root.after(0, lambda p=progress: (
                    self.progress_var.set(p),
                    self.root.update_idletasks()
                ))
            
            try:
                # 特殊处理git+格式的依赖
                if operator == 'git+':
                    # 对于git+格式的依赖，我们不进行版本检查
                    # 因为它们通常是从git仓库直接安装的
                    self.check_results[package_name] = {
                        'installed': False,
                        'required': version,  # version中存储的是完整的git URL
                        'conflict': False,
                        'message': f"包 '{package_name}' 需要从git仓库安装: {version}"
                    }
                    continue
                
                # 检查普通格式包是否已安装及版本
                installed_version = self.get_installed_version(package_name)
                
                if installed_version:
                    conflict = False
                    message = f"包 '{package_name}' 已安装: {installed_version}"
                    
                    # 检查版本冲突
                    if operator and version:
                        if not self.check_version_constraint(installed_version, operator, version):
                            conflict = True
                            self.has_conflicts = True
                            message += f" 但不满足要求: {package_name}{operator}{version}"
                    
                    self.check_results[package_name] = {
                        'installed': True,
                        'version': installed_version,
                        'required': f"{package_name}{operator}{version}" if operator else package_name,
                        'conflict': conflict,
                        'message': message
                    }
                else:
                    self.check_results[package_name] = {
                        'installed': False,
                        'required': f"{package_name}{operator}{version}" if operator else package_name,
                        'conflict': False,
                        'message': f"包 '{package_name}' 未安装，需要安装: {package_name}{operator}{version}" if operator else f"包 '{package_name}' 未安装，需要安装"
                    }
            except Exception as e:
                self.has_conflicts = True
                self.check_results[package_name] = {
                    'error': str(e),
                    'message': f"检查包 '{package_name}' 时出错: {str(e)}"
                }
        
        # 在主线程中显示检查结果
        self.root.after(0, self.show_check_results)
        
        # 根据检查结果更新安装按钮状态
        if not self.has_conflicts and self.dependencies and self.check_results:
            self.install_btn.config(state=tk.NORMAL)
        else:
            self.install_btn.config(state=tk.DISABLED)
    
    def get_installed_version(self, package_name):
        """获取已安装包的版本"""
        # 如果未选择Python环境，返回None
        if not self.python_exe_path:
            return None
            
        try:
            # 使用指定的Python环境的pip show命令获取包信息，隐藏控制台窗口
            kwargs = {
                'capture_output': True,
                'text': True,
                'check': False
            }
            # Windows平台添加creationflags参数隐藏控制台窗口
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            
            result = subprocess.run(
                [self.python_exe_path, '-m', 'pip', 'show', package_name],
                **kwargs
            )
            
            if result.returncode == 0:
                # 从输出中提取版本号
                version_line = next((line for line in result.stdout.split('\n') if line.startswith('Version: ')), None)
                if version_line:
                    return version_line.split('Version: ')[1]
            return None
        except Exception:
            return None
    
    def check_version_constraint(self, installed_version, operator, required_version):
        """检查版本约束是否满足"""
        try:
            # 使用文件顶部定义的parse_version函数
            installed = parse_version(installed_version)
            required = parse_version(required_version)
            
            if operator == '==':
                return installed == required
            elif operator == '>=':
                return installed >= required
            elif operator == '<=':
                return installed <= required
            elif operator == '>':
                return installed > required
            elif operator == '<':
                return installed < required
            elif operator == '!=':
                return installed != required
            # 处理兼容版本操作符 ~=
            elif operator == '~=':
                # ~=x.y 表示 >=x.y, ==x.*
                installed_parts = installed_version.split('.')
                required_parts = required_version.split('.')
                if len(installed_parts) >= 2 and len(required_parts) >= 2:
                    return (installed_parts[0] == required_parts[0] and \
                            int(installed_parts[1]) >= int(required_parts[1]))
                return False
            return False
        except Exception:
            # 如果版本解析失败，返回False表示可能存在冲突
            return False
    
    def show_check_results(self):
        """显示检查结果"""
        conflicts = 0
        errors = 0
        not_installed = 0
        
        self.update_result_text("环境依赖冲突检查结果:\n\n")
        
        for package_name, result in self.check_results.items():
            if 'error' in result:
                self.update_result_text(f"[错误] {result['message']}\n")
                errors += 1
            else:
                if result['conflict']:
                    self.update_result_text(f"[冲突] {result['message']}\n")
                    conflicts += 1
                elif not result['installed']:
                    self.update_result_text(f"[未安装] {result['message']}\n")
                    not_installed += 1
                else:
                    self.update_result_text(f"[正常] {result['message']}\n")
        
        self.update_result_text("\n" + "="*60 + "\n")
        self.update_result_text(f"检查完成！共 {len(self.dependencies)} 个依赖包\n")
        self.update_result_text(f"冲突: {conflicts} 个\n")
        self.update_result_text(f"错误: {errors} 个\n")
        self.update_result_text(f"未安装: {not_installed} 个\n")
        self.update_result_text(f"正常: {len(self.dependencies) - conflicts - errors - not_installed} 个\n")
        
        # 更新冲突状态
        self.has_conflicts = conflicts > 0 or errors > 0
        
        # 在主线程中隐藏进度条
        if self.root._windowingsystem is not None and self.root.winfo_exists():
            self.root.after(0, self.progress_bar.pack_forget)
    
    def start_simulation(self):
        """开始模拟安装"""
        if not self.dependencies:
            messagebox.showwarning("警告", "请先选择并解析requirements.txt文件")
            return
        
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text("开始模拟安装依赖包...\n\n")
        
        # 在新线程中执行模拟安装
        simulate_thread = Thread(target=self.simulate_installation)
        simulate_thread.daemon = True
        simulate_thread.start()
    
    def simulate_installation(self):
        """模拟安装依赖包"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        temp_requirements_path = None
        try:
            # 构建requirements内容，特殊处理git+格式
            requirements_lines = []
            for pkg, op, ver in self.dependencies:
                if op == 'git+':
                    # git+格式依赖直接使用完整URL
                    requirements_lines.append(ver)
                else:
                    requirements_lines.append(f"{pkg}{op}{ver}" if op else pkg)
            
            requirements_content = "\n".join(requirements_lines)
            
            # 创建临时requirements.txt文件
            temp_dir = tempfile.gettempdir()
            temp_requirements_path = os.path.join(temp_dir, f"requirements_{int(time.time())}.txt")
            
            with open(temp_requirements_path, 'w', encoding='utf-8') as f:
                f.write(requirements_content)
            
            # 使用pip install --dry-run模拟安装
            self.update_result_text("正在执行 pip install --dry-run 命令...\n\n")
            
            # 构建命令，添加镜像源参数
            cmd = [self.python_exe_path, '-m', 'pip', 'install', '--dry-run', '-r', temp_requirements_path]
            
            # 添加镜像源参数
            mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
            if mirror_url:
                cmd.extend(['--index-url', mirror_url])
                cmd.append('--trusted-host')
                cmd.append(mirror_url.split('/')[2])  # 添加trusted-host参数
                self.update_result_text(f"使用镜像源: {self.selected_mirror} ({mirror_url})\n\n")
            
            # 设置子进程参数
            kwargs = {
                'stdout': subprocess.PIPE,
                'stderr': subprocess.STDOUT,
                'text': True,
                'bufsize': 1
            }
            # Windows平台添加creationflags参数隐藏控制台窗口
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            
            process = subprocess.Popen(cmd, **kwargs)
            
            # 实时显示输出
            for line in iter(process.stdout.readline, ''):
                self.update_result_text(line)
                self.root.update_idletasks()
            
            process.wait()
            
            self.update_result_text("\n" + "="*60 + "\n")
            
            if process.returncode == 0:
                self.update_result_text("模拟安装成功！没有检测到依赖冲突。\n")
                # 在主线程中显示成功消息
                self.root.after(0, lambda: messagebox.showinfo("成功", "模拟安装成功！没有检测到依赖冲突。"))
            else:
                self.update_result_text("模拟安装失败！检测到依赖冲突或其他问题。\n")
                # 在主线程中显示失败消息
                self.root.after(0, lambda: messagebox.showerror("失败", "模拟安装失败！检测到依赖冲突或其他问题。"))
        except Exception as e:
            self.update_result_text(f"模拟安装时出错: {str(e)}\n")
            # 在主线程中显示错误消息
            self.root.after(0, lambda: messagebox.showerror("错误", f"模拟安装过程中出现错误: {str(e)}"))
        finally:
            # 删除临时文件
            if temp_requirements_path and os.path.exists(temp_requirements_path):
                try:
                    os.remove(temp_requirements_path)
                except:
                    pass
        
        # 在主线程中隐藏进度条
        if self.root._windowingsystem is not None and self.root.winfo_exists():
            self.root.after(0, self.progress_bar.pack_forget)
    
    def view_current_env(self):
        """查看当前Python环境已安装的包"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text("正在获取当前Python环境已安装的包...\n\n")
        
        # 在新线程中执行
        env_thread = Thread(target=self.get_current_environment)
        env_thread.daemon = True
        env_thread.start()
    
    def get_current_environment(self):
        """获取当前Python环境已安装的包"""
        try:
            # 使用指定的Python环境的pip list命令获取已安装的包，隐藏控制台窗口
            kwargs = {
                'capture_output': True,
                'text': True,
                'check': True
            }
            # Windows平台添加creationflags参数隐藏控制台窗口
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            
            result = subprocess.run(
                [self.python_exe_path, '-m', 'pip', 'list', '--format=freeze'],
                **kwargs
            )
            
            packages = result.stdout.strip().split('\n')
            
            self.update_result_text(f"当前环境共安装了 {len(packages)} 个包:\n\n")
            
            # 按字母顺序排序
            packages.sort()
            
            for package in packages:
                self.update_result_text(f"{package}\n")
                # 更新进度条
                progress = (packages.index(package) + 1) / len(packages) * 100
                
                # 在主线程中更新进度条并刷新UI
                if self.root._windowingsystem is not None and self.root.winfo_exists():
                    self.root.after(0, lambda p=progress: (
                        self.progress_var.set(p),
                        self.root.update_idletasks()
                    ))
            
            # 在主线程中询问用户是否保存环境信息到文件
            self.root.after(100, self._ask_save_environment_on_main_thread, packages)
        except Exception as e:
            self.update_result_text(f"获取环境信息时出错: {str(e)}\n")
        
        # 隐藏进度条
        self.progress_bar.pack_forget()
        
    def _ask_save_environment_on_main_thread(self, packages):
        """在主线程中调用ask_save_environment方法"""
        self.ask_save_environment(packages)
        
    def ask_save_environment(self, packages):
        """询问用户是否保存环境信息到文件"""
        # 在主线程中弹出询问对话框
        answer = messagebox.askyesno(
            "保存环境信息",
            "是否将当前Python环境的包列表保存到文本文件？"
        )
        
        if answer:
            # 弹出文件保存对话框，使用格式：当前日期+python环境绝对目录（盘符改为X盘格式，\用-代替）
            current_date = time.strftime('%Y%m%d')
            # 获取Python环境的绝对路径
            env_absolute_path = self.python_env_path if self.python_env_path else "unknown_env"
            
            # 处理路径格式：将盘符从X:改为X盘
            formatted_path = env_absolute_path
            if len(env_absolute_path) >= 2 and env_absolute_path[1] == ':':
                drive_letter = env_absolute_path[0]
                rest_path = env_absolute_path[2:] if len(env_absolute_path) > 2 else ''
                formatted_path = f"{drive_letter}盘{rest_path}"
            
            # 替换路径中的反斜杠为连字符，并处理其他无效字符
            formatted_env_path = formatted_path.replace('\\', '-').replace('/', '-').replace(':', '-').replace('*', '-').replace('?', '-').replace('"', '-').replace('<', '-').replace('>', '-').replace('|', '-')
            
            file_path = filedialog.asksaveasfilename(
                title="保存环境信息",
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*")],
                initialfile=f"{current_date}_{formatted_env_path}_venv_packages.txt"
            )
            
            if file_path:
                try:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        # 写入环境信息头部
                        f.write(f"# Python环境: {self.python_exe_path}\n")
                        f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write(f"# 共安装了 {len(packages)} 个包\n")
                        f.write("#\n# 包列表:\n")
                        # 写入包列表
                        for package in packages:
                            f.write(f"{package}\n")
                    
                    self.update_result_text(f"\n环境信息已成功保存到: {file_path}\n")
                    # 在主线程中显示成功消息
                    self.root.after(0, lambda: messagebox.showinfo("成功", f"环境信息已保存到: {file_path}"))
                except Exception as e:
                        self.update_result_text(f"\n保存环境信息时出错: {str(e)}\n")
                        # 在主线程中显示错误消息
                        self.root.after(0, lambda: messagebox.showerror("错误", f"保存文件失败: {str(e)}"))
    
    def select_python_environment(self):
        """选择Python环境目录"""
        # 先尝试让用户选择Python可执行文件
        python_exe = filedialog.askopenfilename(
            title="选择Python可执行文件",
            filetypes=[("Python可执行文件", "python.exe python3.exe"), ("所有文件", "*")],
            initialdir=os.path.dirname(sys.executable)
        )
        
        if python_exe:
            # 验证选择的是否为有效的Python可执行文件
            if shutil.which(python_exe) or os.path.isfile(python_exe) and os.access(python_exe, os.X_OK):
                self.python_exe_path = python_exe
                self.python_env_path = os.path.dirname(python_exe)
                self.env_label.config(text=f"当前Python环境: {self.python_exe_path}")
                self.update_result_text(f"已切换到Python环境: {self.python_exe_path}\n\n")
                
                # 重置检查结果和安装按钮状态
                self.check_results = {}
                self.install_btn.config(state=tk.DISABLED)
                self.has_conflicts = True
            else:
                # 在主线程中显示错误消息
                self.root.after(0, lambda: messagebox.showerror("错误", f"选择的文件不是有效的Python可执行文件: {python_exe}"))
        else:
            # 如果用户取消选择可执行文件，尝试让用户选择环境目录
            env_dir = filedialog.askdirectory(
                title="选择Python环境目录",
                initialdir=os.path.dirname(os.path.dirname(sys.executable))
            )
            
            if env_dir:
                # 尝试在选择的目录中找到Python可执行文件
                if os.name == 'nt':  # Windows系统
                    python_exe_candidates = [
                        os.path.join(env_dir, 'python.exe'),
                        os.path.join(env_dir, 'Scripts', 'python.exe'),
                        os.path.join(env_dir, 'bin', 'python.exe')
                    ]
                else:  # Unix/Linux系统
                    python_exe_candidates = [
                        os.path.join(env_dir, 'bin', 'python'),
                        os.path.join(env_dir, 'bin', 'python3')
                    ]
                
                python_exe = None
                for candidate in python_exe_candidates:
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        python_exe = candidate
                        break
                
                if python_exe:
                    self.python_exe_path = python_exe
                    self.python_env_path = env_dir
                    self.env_label.config(text=f"当前Python环境: {self.python_exe_path}")
                    self.update_result_text(f"已切换到Python环境: {self.python_exe_path}\n\n")
                    
                    # 重置检查结果和安装按钮状态
                    self.check_results = {}
                    self.install_btn.config(state=tk.DISABLED)
                    self.has_conflicts = True
                else:
                    # 在主线程中显示错误消息
                    self.root.after(0, lambda: messagebox.showerror("错误", f"在选择的目录中未找到有效的Python可执行文件: {env_dir}"))

    def start_installation(self):
        """开始实际安装依赖包"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        if not self.dependencies:
            messagebox.showwarning("警告", "请先选择并解析requirements.txt文件")
            return
        
        if self.has_conflicts:
            messagebox.showwarning("警告", "检测到冲突，无法进行实际安装")
            return
        
        # 确认安装
        confirm = messagebox.askyesno(
            "确认安装",
            f"即将在Python环境 {self.python_exe_path} 中安装 {len(self.dependencies)} 个依赖包，是否继续？"
        )
        
        if not confirm:
            return
        
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text("开始实际安装依赖包...\n\n")
        
        # 在新线程中执行安装
        install_thread = Thread(target=self.perform_installation)
        install_thread.daemon = True
        install_thread.start()

    def perform_installation(self):
        """实际安装依赖包"""
        temp_requirements_path = None
        try:
            # 构建requirements内容，特殊处理git+格式
            requirements_lines = []
            for pkg, op, ver in self.dependencies:
                if op == 'git+':
                    # git+格式依赖直接使用完整URL
                    requirements_lines.append(ver)
                else:
                    requirements_lines.append(f"{pkg}{op}{ver}" if op else pkg)
            
            requirements_content = "\n".join(requirements_lines)
            
            # 创建临时requirements.txt文件
            temp_dir = tempfile.gettempdir()
            temp_requirements_path = os.path.join(temp_dir, f"requirements_{int(time.time())}.txt")
            
            with open(temp_requirements_path, 'w', encoding='utf-8') as f:
                f.write(requirements_content)
            
            # 使用pip install命令实际安装依赖
            self.update_result_text(f"正在执行 pip install -r {temp_requirements_path} 命令...\n\n")
            
            # 构建命令，添加镜像源参数
            cmd = [self.python_exe_path, '-m', 'pip', 'install', '-r', temp_requirements_path]
            
            # 添加镜像源参数
            mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
            if mirror_url:
                cmd.extend(['--index-url', mirror_url])
                cmd.append('--trusted-host')
                cmd.append(mirror_url.split('/')[2])  # 添加trusted-host参数
                self.update_result_text(f"使用镜像源: {self.selected_mirror} ({mirror_url})\n\n")
            
            # 设置子进程参数
            kwargs = {
                'stdout': subprocess.PIPE,
                'stderr': subprocess.STDOUT,
                'text': True,
                'bufsize': 1
            }
            # Windows平台添加creationflags参数隐藏控制台窗口
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            
            process = subprocess.Popen(cmd, **kwargs)
            
            # 实时显示输出
            for line in iter(process.stdout.readline, ''):
                self.update_result_text(line)
                self.root.update_idletasks()
            
            process.wait()
            
            self.update_result_text("\n" + "="*60 + "\n")
            
            if process.returncode == 0:
                self.update_result_text("安装成功！所有依赖包已成功安装。\n")
                # 在主线程中显示成功消息
                self.root.after(0, lambda: messagebox.showinfo("成功", "所有依赖包已成功安装！"))
            else:
                self.update_result_text("安装失败！请查看输出信息了解详细错误。\n")
                # 在主线程中显示失败消息
                self.root.after(0, lambda: messagebox.showerror("失败", "安装过程中出现错误，请查看输出信息。"))
        except Exception as e:
            self.update_result_text(f"安装时出错: {str(e)}\n")
            # 在主线程中显示错误消息
            self.root.after(0, lambda: messagebox.showerror("错误", f"安装过程中出现错误: {str(e)}"))
        finally:
            # 删除临时文件
            if temp_requirements_path and os.path.exists(temp_requirements_path):
                try:
                    os.remove(temp_requirements_path)
                except:
                    pass
        
        # 在主线程中隐藏进度条并重新检查环境
        if self.root._windowingsystem is not None and self.root.winfo_exists():
            self.root.after(0, lambda: (
                self.progress_bar.pack_forget(),
                self.start_checking()
            ))

    def clear_results(self):
        """清空结果文本框"""
        self.result_text.delete(1.0, tk.END)
        
    def compare_environment_files(self):
        """比较两个环境文件的差异"""
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text("开始比较环境文件...\n\n")
        
        # 在新线程中执行比较，避免界面卡顿
        compare_thread = Thread(target=self.perform_file_comparison)
        compare_thread.daemon = True
        compare_thread.start()
        
    def perform_file_comparison(self):
        """执行文件比较操作"""
        # 先清空结果
        self.root.after(0, lambda: self.result_text.delete(1.0, tk.END))
        
        # 选择第一个文件（安装前的环境文件）
        file1_path = None
        file2_path = None
        
        # 使用主线程弹出文件选择对话框
        self.root.after(0, lambda: self._select_file_for_comparison(1))
        
        # 等待文件选择完成
        while not hasattr(self, 'selected_file1'):
            time.sleep(0.1)
            if not self.root.winfo_exists():
                return
        
        file1_path = self.selected_file1
        delattr(self, 'selected_file1')
        
        if not file1_path:
            self.update_result_text("操作已取消\n")
            self.root.after(0, self.progress_bar.pack_forget)
            return
        
        # 选择第二个文件（安装后的环境文件）
        self.root.after(0, lambda: self._select_file_for_comparison(2))
        
        # 等待文件选择完成
        while not hasattr(self, 'selected_file2'):
            time.sleep(0.1)
            if not self.root.winfo_exists():
                return
        
        file2_path = self.selected_file2
        delattr(self, 'selected_file2')
        
        if not file2_path:
            self.update_result_text("操作已取消\n")
            self.root.after(0, self.progress_bar.pack_forget)
            return
        
        # 更新进度
        self.root.after(0, lambda: self.progress_var.set(30))
        
        try:
            # 读取两个文件的内容
            self.update_result_text(f"正在比较文件:\n{file1_path}\n和\n{file2_path}\n\n")
            
            # 解析文件内容，提取包信息
            packages1 = self._parse_environment_file(file1_path)
            packages2 = self._parse_environment_file(file2_path)
            
            # 更新进度
            self.root.after(0, lambda: self.progress_var.set(60))
            
            # 比较两个文件的差异
            added, removed, changed = self._compare_environment_packages(packages1, packages2)
            
            # 显示比较结果
            self._show_comparison_results(file1_path, file2_path, added, removed, changed)
            
        except Exception as e:
            self.update_result_text(f"比较文件时出错: {str(e)}\n")
        finally:
            # 更新进度并隐藏进度条
            self.root.after(0, lambda: (
                self.progress_var.set(100),
                self.progress_bar.pack_forget()
            ))
            
    def _select_file_for_comparison(self, file_number):
        """在主线程中选择比较文件"""
        title = f"选择环境文件 {file_number}"
        file_path = filedialog.askopenfilename(
            title=title,
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*")]
        )
        
        if file_number == 1:
            self.selected_file1 = file_path
        else:
            self.selected_file2 = file_path
            
    def _parse_environment_file(self, file_path):
        """解析环境文件，提取包信息"""
        packages = {}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            for line in lines:
                line = line.strip()
                # 跳过注释和空行
                if line.startswith('#') or not line:
                    continue
                
                # 解析包名和版本
                if '==' in line:
                    parts = line.split('==')
                    if len(parts) == 2:
                        package_name = parts[0].strip()
                        version = parts[1].strip()
                        packages[package_name] = version
                elif '>=' in line or '<=' in line or '>' in line or '<' in line or '!=' in line:
                    # 处理其他版本约束格式，但只提取包名
                    import re
                    match = re.match(r'^([a-zA-Z0-9_-]+)', line)
                    if match:
                        package_name = match.group(1)
                        # 对于非==格式的依赖，我们只记录包名和完整的版本约束
                        packages[package_name] = line
                else:
                    # 没有版本约束的情况
                    packages[line] = ''
        except Exception as e:
            self.update_result_text(f"解析文件 {file_path} 时出错: {str(e)}\n")
        
        return packages
        
    def _compare_environment_packages(self, packages1, packages2):
        """比较两个环境的包信息，返回差异"""
        added = []      # 在packages2中但不在packages1中的包
        removed = []    # 在packages1中但不在packages2中的包
        changed = []    # 在两个环境中但版本不同的包
        
        # 检查哪些包被添加了
        for package_name, version2 in packages2.items():
            if package_name not in packages1:
                added.append((package_name, version2))
            elif packages1[package_name] != version2:
                changed.append((package_name, packages1[package_name], version2))
        
        # 检查哪些包被移除了
        for package_name, version1 in packages1.items():
            if package_name not in packages2:
                removed.append((package_name, version1))
        
        return added, removed, changed
        
    def _show_comparison_results(self, file1_path, file2_path, added, removed, changed):
        """显示比较结果"""
        self.update_result_text("="*60 + "\n")
        self.update_result_text(f"环境文件比较结果\n")
        self.update_result_text(f"文件1: {os.path.basename(file1_path)}\n")
        self.update_result_text(f"文件2: {os.path.basename(file2_path)}\n")
        self.update_result_text("="*60 + "\n\n")
        
        # 显示添加的包
        if added:
            self.update_result_text(f"新添加的包 ({len(added)} 个):\n")
            for package_name, version in added:
                self.update_result_text(f"  + {package_name}=={version}\n")
            self.update_result_text("\n")
        
        # 显示移除的包
        if removed:
            self.update_result_text(f"移除的包 ({len(removed)} 个):\n")
            for package_name, version in removed:
                self.update_result_text(f"  - {package_name}=={version}\n")
            self.update_result_text("\n")
        
        # 显示版本变化的包
        if changed:
            self.update_result_text(f"版本变化的包 ({len(changed)} 个):\n")
            for package_name, old_version, new_version in changed:
                self.update_result_text(f"  * {package_name}: {old_version} -> {new_version}\n")
            self.update_result_text("\n")
        
        # 如果没有差异
        if not added and not removed and not changed:
            self.update_result_text("两个环境文件完全相同，没有差异！\n")
        
        self.update_result_text("="*60 + "\n")
        self.update_result_text(f"比较完成！\n")

    def update_result_text(self, text):
        """更新结果文本框，确保在主线程中执行"""
        def _update_text():
            self.result_text.insert(tk.END, text)
            self.result_text.see(tk.END)
        
        # 检查是否在主线程中，如果不是则通过after在主线程中执行
        if self.root._windowingsystem is not None and self.root.winfo_exists():
            self.root.after(0, _update_text)
        else:
            # 如果窗口已关闭，不进行更新
            pass
            
    def on_mirror_change(self, event=None):
        """处理镜像源选择变化"""
        self.selected_mirror = self.mirror_var.get()
        mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
        self.update_result_text(f"已切换到镜像源: {self.selected_mirror}{' (' + mirror_url + ')' if mirror_url else ''}\n\n")
        
    def test_mirror_speed(self):
        """测试镜像源速度"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text("开始测试镜像源速度...\n\n")
        
        # 在新线程中执行测试
        test_thread = Thread(target=self._perform_mirror_test)
        test_thread.daemon = True
        test_thread.start()
        
    def _perform_mirror_test(self):
        """执行镜像源测试并返回测试结果"""
        results = []
        total_mirrors = len(PYPI_MIRRORS)
        
        for i, (mirror_name, mirror_url) in enumerate(PYPI_MIRRORS.items()):
            # 更新进度条
            progress = (i + 1) / total_mirrors * 100
            if self.root._windowingsystem is not None and self.root.winfo_exists():
                self.root.after(0, lambda p=progress: (
                    self.progress_var.set(p),
                    self.root.update_idletasks()
                ))
            
            self.update_result_text(f"正在测试 {mirror_name}...\n")
            
            try:
                # 首先尝试使用urllib直接测试HTTP连接，这是最基本的连通性测试
                if mirror_url:
                    # 对于非官方源，先尝试HTTP连接测试
                    start_time = time.time()
                    connected = self._test_url_connectivity(mirror_url)
                    elapsed_time = time.time() - start_time
                    
                    if connected:
                        results.append((mirror_name, elapsed_time))
                        self.update_result_text(f"{mirror_name} 连接成功，响应时间: {elapsed_time:.2f}秒\n\n")
                        continue  # 如果HTTP连接测试成功，就不再执行pip测试
                    else:
                        self.update_result_text(f"{mirror_name} HTTP连接测试失败，尝试pip命令测试...\n")
                
                # 如果HTTP连接测试失败或没有URL（官方源），则使用pip命令测试
                start_time = time.time()
                
                # 使用简单的pip命令测试
                cmd = [self.python_exe_path, '-m', 'pip', 'install', '--dry-run', 'pip']
                
                # 如果有镜像源URL，添加index-url和trusted-host参数
                if mirror_url:
                    cmd.extend(['--index-url', mirror_url])
                    # 添加trusted-host参数以避免SSL验证问题
                    host = mirror_url.split('/')[2]  # 获取主机名
                    cmd.extend(['--trusted-host', host])
                
                kwargs = {
                    'stdout': subprocess.PIPE,
                    'stderr': subprocess.STDOUT,
                    'text': True,
                    'timeout': 15  # 增加超时时间到15秒
                }
                
                if os.name == 'nt':
                    kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                
                result = subprocess.run(cmd, **kwargs)
                
                elapsed_time = time.time() - start_time
                
                # 只要命令执行成功（返回码为0），就认为镜像源可用
                if result.returncode == 0:
                    results.append((mirror_name, elapsed_time))
                    self.update_result_text(f"{mirror_name} 测试成功，响应时间: {elapsed_time:.2f}秒\n\n")
                else:
                    # 捕获并显示错误输出的前几行，帮助用户了解失败原因
                    error_output = result.stdout.strip().split('\n')[:3] if result.stdout else []
                    error_summary = '\n'.join(error_output)
                    self.update_result_text(f"{mirror_name} 测试失败: 返回代码 {result.returncode}\n")
                    if error_summary:
                        self.update_result_text(f"错误摘要: {error_summary}\n")
                    self.update_result_text("\n")
            except subprocess.TimeoutExpired:
                self.update_result_text(f"{mirror_name} 测试超时（>15秒）\n\n")
            except Exception as e:
                self.update_result_text(f"{mirror_name} 测试出错: {str(e)}\n\n")
        
        # 显示测试结果排序
        if results:
            # 按响应时间排序
            results.sort(key=lambda x: x[1])
            
            self.update_result_text("\n" + "="*60 + "\n")
            self.update_result_text("镜像源速度测试结果（从快到慢）:\n\n")
            
            for i, (mirror_name, elapsed_time) in enumerate(results):
                self.update_result_text(f"{i+1}. {mirror_name}: {elapsed_time:.2f}秒\n")
                
            # 自动选择并应用最快的镜像源
            fastest_mirror = results[0][0]
            self.update_result_text(f"\n已自动选择最快的镜像源: {fastest_mirror}\n")
            
            # 直接设置最快的镜像源，无需用户确认
            self.root.after(0, lambda: (
                self.mirror_var.set(fastest_mirror),
                self.on_mirror_change()
            ))
        
        # 隐藏进度条
        if self.root._windowingsystem is not None and self.root.winfo_exists():
            self.root.after(0, self.progress_bar.pack_forget)
    
    def _test_url_connectivity(self, url):
        """使用urllib测试URL的连通性"""
        try:
            import urllib.request
            import ssl
            
            # 创建不验证SSL证书的上下文（避免自签名证书问题）
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            # 设置超时时间
            timeout = 5
            
            # 发送HEAD请求测试连通性
            with urllib.request.urlopen(url, context=context, timeout=timeout) as response:
                # 检查响应状态码
                return response.status < 400
        except Exception:
            return False
            


if __name__ == "__main__":
    # 实现单例应用功能
    import atexit
    import sys
    
    # 根据不同操作系统选择不同的单例实现方式
    is_single_instance = True
    lock_file = None
    lock_file_path = os.path.join(tempfile.gettempdir(), 'environment_checker.lock')
    
    try:
        if sys.platform.startswith('win'):
            # Windows平台实现
            import msvcrt
            lock_file = open(lock_file_path, 'w')
            # 使用非阻塞锁
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except:
                is_single_instance = False
        else:
            # Unix/Linux/Mac平台实现
            import fcntl
            lock_file = open(lock_file_path, 'w')
            # 使用非阻塞锁
            try:
                fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except:
                is_single_instance = False
        
        # 确保程序退出时释放锁文件
        def release_lock():
            try:
                if lock_file:
                    if sys.platform.startswith('win'):
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    lock_file.close()
                if os.path.exists(lock_file_path):
                    os.remove(lock_file_path)
            except:
                pass
        
        atexit.register(release_lock)
        
    except Exception:
        is_single_instance = False
    
    if not is_single_instance:
        # 程序已在运行，显示错误消息
        temp_root = tk.Tk()
        temp_root.withdraw()  # 隐藏主窗口
        messagebox.showerror("程序已在运行", "Environment Checker 程序已经在运行中，不能同时打开多个实例。")
        temp_root.destroy()
        sys.exit(1)
    
    # 继续正常的程序启动流程
    root = tk.Tk()
    
    # 设置窗口图标，支持打包成exe后正确加载
    try:
        # 确定图标文件的绝对路径
        import sys
        import os
        
        # 检查是否是pyinstaller打包的exe文件
        if hasattr(sys, '_MEIPASS'):
            # 如果是exe文件，使用_MEIPASS路径
            icon_path = os.path.join(sys._MEIPASS, 'favicon.ico')
        else:
            # 否则使用当前工作目录
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'favicon.ico')
        
        # 检查图标文件是否存在
        if os.path.exists(icon_path):
            root.iconbitmap(icon_path)
        else:
            # 如果图标文件不存在，尝试使用相对路径
            try:
                root.iconbitmap("favicon.ico")
            except Exception:
                # 图标加载失败不影响程序运行
                pass
    except Exception:
        # 图标加载失败不影响程序运行
        pass
    
    app = EnvironmentCheckerApp(root)
    root.mainloop()