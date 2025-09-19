# 导入必要的模块
import sys
import os
import json
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog

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

# 配置项类 - 将分散的配置集中管理
class Config:
    VERSION = "V2.3"
    LOCK_FILE_PATH = os.path.join(tempfile.gettempdir(), 'environment_checker.lock')
    MAX_THREAD_WORKERS = 4  # 最大线程数量
    DEFAULT_TIMEOUT = 30  # 默认超时时间(秒)
    MAX_HISTORY_ITEMS = 20  # 库名称历史记录最大数量

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
        
    def _get_subprocess_kwargs(self, timeout=None, capture_output=False, pipe_stdout=False):
        """获取用于subprocess.Popen的子进程运行参数，自动处理Windows平台隐藏控制台窗口
           注意：timeout参数不会添加到返回的kwargs中，因为subprocess.Popen不支持该参数
        """
        kwargs = {
            'text': True,
        }
        
        if capture_output:
            kwargs['capture_output'] = True
            kwargs['check'] = False
        elif pipe_stdout:
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.STDOUT
            kwargs['bufsize'] = 1
        
        # Windows平台添加creationflags参数隐藏控制台窗口
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        
        return kwargs
        
    def _get_run_subprocess_kwargs(self, timeout=Config.DEFAULT_TIMEOUT, capture_output=False, check=False):
        """获取用于subprocess.run的子进程运行参数，包含timeout和check参数"""
        kwargs = self._get_subprocess_kwargs(capture_output=capture_output)
        if timeout is not None:
            kwargs['timeout'] = timeout
        kwargs['check'] = check
        return kwargs
        
    def __init__(self, root):
        self.root = root
        self.root.title(f"ComfyUI中Python环境维护小工具 {Config.VERSION} 练老师 QQ群: 723799422")
        self.root.geometry("1200x770")
        self.root.minsize(1000, 760)
        
        # 设置窗口图标
        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'favicon.ico')
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception as e:
            # 如果设置图标失败，静默忽略错误
            pass
        
        # 设置窗口在桌面中央显示
        self.root.update_idletasks()  # 更新窗口大小信息
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        
        # 设置中文字体支持 - 优化字体大小
        self.style = ttk.Style()
        self.style.configure(
            "TButton",
            font=("SimHei", 11)
        )
        self.style.configure(
            "TLabel",
            font=("SimHei", 11)
        )
        
        # 初始化所有必要的属性
        # 存储requirements.txt路径
        self.requirements_path = ""
        # 存储Python环境路径
        self.python_env_path = ""
        # 存储Python可执行文件路径
        self.python_exe_path = ""
        # 存储依赖包列表
        self.dependencies = []
        # 存储检查结果
        self.check_results = {}
        # 存储历史使用过的库名称
        self.lib_history = []
        # 标记是否有冲突
        self.has_conflicts = True
        # 存储当前选择的镜像源
        self.selected_mirror = "官方源"
        # 进度条变量
        self.progress_var = tk.DoubleVar()
        # Python环境路径列表
        self.python_paths = []
        # Python环境JSON文件路径 - 使用当前工作目录，确保打包成exe后能在运行目录生成文件
        self.python_addr_file = os.path.join(os.getcwd(), 'python_addr.json')
        
        # 初始化Python环境列表
        self._init_python_environments()
        
        # 创建界面（在所有属性初始化后）
        self.create_widgets()
    
    def _init_python_environments(self):
        """初始化Python环境列表，仅从文件读取"""
        # 如果文件存在，读取已保存的Python环境路径
        if os.path.exists(self.python_addr_file):
            try:
                with open(self.python_addr_file, 'r', encoding='utf-8') as f:
                    self.python_paths = json.load(f)
            except Exception as e:
                print(f"读取python_addr.json文件失败: {e}")
                self.python_paths = []
        else:
            # 文件不存在时，初始化空列表
            self.python_paths = []
        
        # 如果有保存的环境，设置默认Python环境
        if self.python_paths:
            self.python_exe_path = self.python_paths[0]
            self.python_env_path = os.path.dirname(self.python_exe_path)
        else:
            # 没有保存的环境时，设置为空
            self.python_exe_path = ""
            self.python_env_path = ""
      
    def _save_python_environments(self):
        """保存Python环境列表到文件"""
        try:
            with open(self.python_addr_file, 'w', encoding='utf-8') as f:
                json.dump(self.python_paths, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存python_addr.json文件失败: {e}")
        
    def create_widgets(self):
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建左右分栏的PanedWindow
        paned_window = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 创建左侧面板 - 包含配置和操作功能
        left_frame = ttk.LabelFrame(paned_window, text="配置与操作", padding="12")
        paned_window.add(left_frame, weight=1)
        
        # 创建右侧面板 - 包含结果显示
        right_frame = ttk.LabelFrame(paned_window, text="结果显示", padding="12")
        paned_window.add(right_frame, weight=2)
        
        # === 左侧面板内容 ===

        # 镜像源选择区域 - 移至窗口第一行，调整为与其他子界面一致的grid布局
        mirror_frame = ttk.LabelFrame(left_frame, text="PyPI镜像源", padding="12")
        mirror_frame.pack(fill=tk.X, pady=5)
        
        # 创建标签和下拉框容器
        mirror_label_container = ttk.Frame(mirror_frame)
        mirror_label_container.pack(fill=tk.X, pady=2)
        
        mirror_label = ttk.Label(mirror_label_container, text="选择PyPI镜像源:", width=15)
        mirror_label.pack(side=tk.LEFT, padx=(5, 0))
        
        # 创建镜像源下拉框
        self.mirror_var = tk.StringVar(value=self.selected_mirror)
        self.mirror_combobox = ttk.Combobox(mirror_label_container, textvariable=self.mirror_var, values=list(PYPI_MIRRORS.keys()), state="readonly", width=20)
        self.mirror_combobox.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.mirror_combobox.bind("<<ComboboxSelected>>", self.on_mirror_change)
        
        # 添加镜像源测试按钮
        test_mirror_btn = ttk.Button(mirror_frame, text="测试镜像源", command=self.test_mirror_speed, width=12)
        test_mirror_btn.pack(side=tk.LEFT, padx=(5, 5), pady=5)

        file_frame = ttk.LabelFrame(left_frame, text="插件节点依赖文件", padding="12")
        file_frame.pack(fill=tk.X, pady=5)
        
        # 创建标签容器
        label_container = ttk.Frame(file_frame)
        label_container.pack(fill=tk.X, pady=2)
        
        file_label = ttk.Label(label_container, text="文件路径:", width=10)
        file_label.pack(side=tk.LEFT, padx=(5, 0))

        self.file_label = ttk.Label(label_container, text="未选择requirements.txt文件", anchor=tk.W)
        self.file_label.pack(side=tk.LEFT, padx=(2, 5), fill=tk.X, expand=True)

        select_btn = ttk.Button(file_frame, text="选择文件", command=self.select_requirements_file, width=12)
        select_btn.pack(side=tk.LEFT, padx=(5, 5), pady=5)
        
        # Python环境选择区域 - 调整为下拉列表
        env_frame = ttk.LabelFrame(left_frame, text="当前Python环境", padding="12")
        env_frame.pack(fill=tk.X, pady=5)
        
        # 创建标签容器
        env_label_container = ttk.Frame(env_frame)
        env_label_container.pack(fill=tk.X, pady=2)
        
        env_label = ttk.Label(env_label_container, text="当前环境:", width=10)
        env_label.pack(side=tk.LEFT, padx=(5, 0))

        # 创建Python环境下拉列表
        self.python_env_var = tk.StringVar(value="" if not self.python_exe_path else self.python_exe_path)
        self.python_env_combobox = ttk.Combobox(
            env_label_container, 
            textvariable=self.python_env_var, 
            values=self.python_paths,
            state="readonly", 
            width=50
        )
        self.python_env_combobox.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.python_env_combobox.bind("<<ComboboxSelected>>", self.on_python_env_change)

        # 添加按钮用于选择新的Python环境
        add_env_btn = ttk.Button(env_frame, text="添加环境", command=self.select_python_environment, width=12)
        add_env_btn.pack(side=tk.LEFT, padx=(5, 5), pady=5)
        
        # 添加按钮用于删除选择的Python环境
        delete_env_btn = ttk.Button(env_frame, text="删除环境", command=self.delete_python_environment, width=12)
        delete_env_btn.pack(side=tk.LEFT, padx=(5, 5), pady=5)
        
        # 中间按钮区域 - 改为与第三方库相同的水平布局
        btn_frame = ttk.LabelFrame(left_frame, text="ComfyUI环境操作按钮", padding="12")
        btn_frame.pack(fill=tk.X, pady=8)

        # 创建按钮容器，使用pack布局水平排列
        btn_container = ttk.Frame(btn_frame)
        btn_container.pack(fill=tk.X, expand=True)

        # 计算按钮宽度以适应窗口宽度
        btn_width = 12

        # 第一行按钮
        check_btn = ttk.Button(btn_container, text="依赖是否冲突", command=self.start_checking, width=btn_width)
        check_btn.pack(side=tk.LEFT, padx=8, pady=2)

        simulate_btn = ttk.Button(btn_container, text="依赖模拟安装", command=self.start_simulation, width=btn_width)
        simulate_btn.pack(side=tk.LEFT, padx=8, pady=2)

        view_btn = ttk.Button(btn_container, text="查看当前环境", command=self.view_current_env, width=btn_width)
        view_btn.pack(side=tk.LEFT, padx=8, pady=2)

        # 第二行按钮
        btn_container2 = ttk.Frame(btn_frame)
        btn_container2.pack(fill=tk.X, expand=True)

        # 实际安装按钮
        self.install_btn = ttk.Button(btn_container2, text="依赖实际安装", command=self.start_installation, width=btn_width)
        self.install_btn.pack(side=tk.LEFT, padx=8, pady=2)

        # 比较环境文件按钮
        compare_btn = ttk.Button(btn_container2, text="比较两次环境", command=self.compare_environment_files, width=btn_width)
        compare_btn.pack(side=tk.LEFT, padx=8, pady=2)

        # 查找冲突库按钮
        conflict_lib_btn = ttk.Button(btn_container2, text="查找环境冲突", command=self.find_conflicting_libraries, width=btn_width)
        conflict_lib_btn.pack(side=tk.LEFT, padx=8, pady=2)
        
        # 添加一个占位元素，将清空信息按钮推到右侧
        ttk.Label(btn_container2).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 清空信息按钮 - 移到第二行最后
        clear_btn = ttk.Button(btn_container2, text="清空右边信息", command=self.clear_results, width=btn_width)
        clear_btn.pack(side=tk.LEFT, padx=8, pady=2)

        # 第三方库管理区域 - 优化布局
        lib_frame = ttk.LabelFrame(left_frame, text="第三方库管理", padding="12")
        lib_frame.pack(fill=tk.X, pady=8)
        
        # 库名称和版本输入区域
        lib_input_frame = ttk.Frame(lib_frame)
        lib_input_frame.pack(fill=tk.X, pady=5)
        
        # 库名称下拉框 - 替换原来的输入框，支持历史记录
        lib_name_label = ttk.Label(lib_input_frame, text="环境库名:", width=9)
        lib_name_label.pack(side=tk.LEFT, padx=(0, 5), fill=tk.Y, expand=True)
        self.lib_name_var = tk.StringVar()
        # 创建下拉框，设置为可编辑模式，这样既可以选择历史记录也可以输入新名称
        self.lib_name_combobox = ttk.Combobox(lib_input_frame, textvariable=self.lib_name_var, width=30, state="normal")
        self.lib_name_combobox.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        # 设置下拉框样式
        self.style.configure(
            "TCombobox",
            font=("SimHei", 11)
        )
        
        # 版本选择下拉框
        version_label = ttk.Label(lib_input_frame, text="版本号:", width=7)
        version_label.pack(side=tk.LEFT, padx=5, fill=tk.Y, expand=True)
        self.version_var = tk.StringVar()
        self.version_combobox = ttk.Combobox(lib_input_frame, textvariable=self.version_var, width=20, state="readonly")
        self.version_combobox.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # 功能按钮区域 - 优化按钮间距和大小
        lib_btn_frame = ttk.Frame(lib_frame)
        lib_btn_frame.pack(fill=tk.X, pady=2)
        
        # 计算按钮宽度以适应窗口宽度
        btn_width = 8
        
        search_lib_btn = ttk.Button(lib_btn_frame, text="精确查找", command=self.search_library_exact, width=btn_width)
        search_lib_btn.pack(side=tk.LEFT, padx=5, pady=2)
              
        search_lib_fuzzy_btn = ttk.Button(lib_btn_frame, text="模糊查找", command=self.search_library_local, width=btn_width)
        search_lib_fuzzy_btn.pack(side=tk.LEFT, padx=5, pady=2)
              
        install_lib_btn = ttk.Button(lib_btn_frame, text="安装库", command=self.install_library, width=btn_width)
        install_lib_btn.pack(side=tk.LEFT, padx=5, pady=2)
        
        uninstall_lib_btn = ttk.Button(lib_btn_frame, text="删除库", command=self.uninstall_library, width=btn_width)
        uninstall_lib_btn.pack(side=tk.LEFT, padx=5, pady=2)
        
        # 添加轮子安装和编译安装按钮
        install_whl_btn = ttk.Button(lib_btn_frame, text="轮子安装", command=self.install_whl_file, width=btn_width)
        install_whl_btn.pack(side=tk.LEFT, padx=5, pady=2)
        
        install_source_btn = ttk.Button(lib_btn_frame, text="编译安装", command=self.install_source_code, width=btn_width)
        install_source_btn.pack(side=tk.LEFT, padx=5, pady=2)
        
        # 命令执行区域 - 在第三方库管理下方添加命令执行文本框
        cmd_frame = ttk.LabelFrame(left_frame, text="python -m pip install 命令的相关参数", padding="12")
        cmd_frame.pack(fill=tk.X, pady=8)
        
        # 命令输入区域
        cmd_input_frame = ttk.Frame(cmd_frame)
        cmd_input_frame.pack(fill=tk.X, pady=5)
        
        # 命令输入标签
        cmd_label = ttk.Label(cmd_input_frame, text="CMD:", width=4)
        cmd_label.pack(side=tk.LEFT, padx=(0, 5), fill=tk.Y, expand=True)
        
        # 命令输入文本框
        self.cmd_var = tk.StringVar()
        self.cmd_entry = ttk.Entry(cmd_input_frame, textvariable=self.cmd_var, width=46)
        self.cmd_entry.pack(side=tk.LEFT, padx=0, fill=tk.X, expand=True)
        self.cmd_entry.bind('<Return>', lambda event: self.execute_command())
        
        # 执行命令按钮
        execute_btn = ttk.Button(cmd_input_frame, text="执行", command=self.execute_command, width=5)
        execute_btn.pack(side=tk.LEFT, padx=5, pady=2)
        
        # 显示pip参数说明按钮
        param_btn = ttk.Button(cmd_input_frame, text="参数说明", command=self.show_pip_params, width=8)
        param_btn.pack(side=tk.LEFT, padx=5, pady=2)

        # === 右侧面板内容 ===
        
        # 结果显示区域 - 优化文本框大小和字体
        result_frame = ttk.Frame(right_frame, padding="5")
        result_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建带滚动条的文本框
        # 使用Frame包裹文本框和滚动条，确保布局稳定
        text_frame = ttk.Frame(result_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建文本框，增加选择背景色
        self.result_text = tk.Text(text_frame, wrap=tk.WORD, font=('SimHei', 11),
                                  bg="white", selectbackground="#a6a6a6", selectforeground="black")  # 显式设置白色背景，移除撤销功能
        self.result_text.grid(row=0, column=0, sticky="nsew")
        
        # 配置grid权重，使文本框能够随窗口大小调整
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)
        
        # 垂直滚动条 - 保存为实例变量
        self.v_scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.result_text.yview)
        self.v_scrollbar.grid(row=0, column=1, sticky="ns")
        
        # 水平滚动条 - 保存为实例变量
        self.h_scrollbar = ttk.Scrollbar(result_frame, orient=tk.HORIZONTAL, command=self.result_text.xview)
        self.h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # 配置文本框与滚动条的联动
        self.result_text.config(yscrollcommand=self.v_scrollbar.set)
        self.result_text.config(xscrollcommand=self.h_scrollbar.set)
        
        # 添加选择文本复制功能
        self.result_text.bind('<Control-c>', self.copy_selected_text)
        # 确保文本框可以被选中
        self.result_text.config(state=tk.NORMAL)
        
        # === 进度条放在主框架底部 ===
        # 创建进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_bar.pack_forget()  # 初始隐藏
        
    def select_requirements_file(self):
        """选择requirements.txt文件并显示其内容"""
        file_path = filedialog.askopenfilename(
            title="选择插件节点依赖文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*")]
        )
        
        if file_path:
            self.requirements_path = file_path
            self.file_label.config(text=f"{file_path}")
            
            # 显示文件原始内容
            try:
                # 清空当前文本框内容
                self.clear_results()
                
                # 显示文件路径信息
                self.update_result_text(f"插件节点依赖文件: {os.path.basename(file_path)}\n")
                self.update_result_text(f"路径: {file_path}\n")
                self.update_result_text("="*60 + "\n\n")
                
                # 读取并显示文件内容
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                self.update_result_text(file_content)
                
                # 解析requirements.txt
                self.parse_requirements_file()
                self.update_result_text(f"\n\n" + "="*60 + "\n")
                self.update_result_text(f"成功解析依赖文件，共找到 {len(self.dependencies)} 个依赖包。\n")
                
            except Exception as e:
                # 在主线程中显示错误消息
                self.root.after(0, lambda error=str(e): messagebox.showerror("错误", f"读取文件失败: {error}"))
            
            # 重置安装按钮状态
            self.install_btn.config(state=tk.NORMAL)
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
            self.install_btn.config(state=tk.NORMAL)
    
    def get_installed_version(self, package_name):
        """获取已安装包的版本"""
        # 如果未选择Python环境，返回None
        if not self.python_exe_path:
            return None
            
        try:
            # 使用辅助方法获取子进程参数
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            
            # 使用指定的Python环境的pip show命令获取包信息
            
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
            
            # 处理镜像源参数（如果没有选择或配置，不使用镜像源）
            mirror_url = ''
            if hasattr(self, 'selected_mirror') and hasattr(self, 'PYPI_MIRRORS'):
                mirror_url = self.PYPI_MIRRORS.get(self.selected_mirror, '')
            if mirror_url:
                cmd.extend(['--index-url', mirror_url])
                cmd.append('--trusted-host')
                cmd.append(mirror_url.split('/')[2])  # 添加trusted-host参数
                self.update_result_text(f"使用镜像源: {self.selected_mirror} ({mirror_url})\n\n")
            
            # 使用辅助方法获取子进程参数
            kwargs = self._get_subprocess_kwargs(pipe_stdout=True)
            
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
            kwargs = self._get_run_subprocess_kwargs(capture_output=True, check=True, timeout=Config.DEFAULT_TIMEOUT)
            
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
            # 弹出文件保存对话框，使用格式：当前日期时间+python环境绝对目录（盘符改为X盘格式，\用-代替）
            current_datetime = time.strftime('%Y%m%d_%H%M%S')
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
                initialfile=f"{current_datetime}_{formatted_env_path}.txt"
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
    
    def on_python_env_change(self, event):
        """处理Python环境下拉列表选择变更"""
        selected_python = self.python_env_var.get()
        if selected_python and selected_python != "未选择":
            # 验证选择的是否为有效的Python可执行文件
            if os.path.isfile(selected_python) and os.access(selected_python, os.X_OK):
                self.python_exe_path = selected_python
                self.python_env_path = os.path.dirname(selected_python)
                self.update_result_text(f"已切换到Python环境: {self.python_exe_path}\n\n")
                
                # 重置检查结果和安装按钮状态
                self.check_results = {}
                self.install_btn.config(state=tk.NORMAL)
                self.has_conflicts = True
            else:
                # 在主线程中显示错误消息
                self.root.after(0, lambda: messagebox.showerror("错误", f"选择的文件不是有效的Python可执行文件: {selected_python}"))
    
    def delete_python_environment(self):
        """删除选择的Python环境"""
        selected_python = self.python_env_var.get()
        
        if selected_python and selected_python != "" and selected_python in self.python_paths:
            # 显示确认对话框
            confirmed = messagebox.askyesno(
                "确认删除", 
                f"确定要删除Python环境: {selected_python}吗？\n此操作不可撤销。"
            )
            
            if confirmed:
                # 从列表中删除
                self.python_paths.remove(selected_python)
                
                # 如果删除的是当前使用的环境，重置相关属性
                if selected_python == self.python_exe_path:
                    self.python_exe_path = ""
                    self.python_env_path = ""
                    self.check_results = {}
                    self.has_conflicts = True
                
                # 无论是否删除的是当前环境，都更新下拉列表状态
                # 当下拉列表为空时，设置值为空字符串
                self.python_env_var.set("" if not self.python_paths else 
                                       (self.python_paths[0] if selected_python == self.python_exe_path else self.python_env_var.get()))
                
                # 更新下拉列表的值列表
                self.python_env_combobox['values'] = self.python_paths
                
                # 保存到文件
                self._save_python_environments()
                
                # 显示删除成功消息
                self.update_result_text(f"已删除Python环境: {selected_python}\n\n")
        else:
            # 没有选择环境或选择的环境不在列表中
            messagebox.showinfo("提示", "请先选择要删除的Python环境")
    
    def select_python_environment(self):
        """添加新的Python环境"""
        # 先尝试让用户选择Python可执行文件
        python_exe = filedialog.askopenfilename(
            title="选择Python可执行文件",
            filetypes=[("Python可执行文件", "python.exe python3.exe"), ("所有文件", "*")],
            initialdir=os.path.dirname(sys.executable) if self.python_exe_path else ""
        )
        
        if python_exe:
            # 验证选择的是否为有效的Python可执行文件
            if shutil.which(python_exe) or os.path.isfile(python_exe) and os.access(python_exe, os.X_OK):
                # 检查是否已经存在于列表中
                if python_exe not in self.python_paths:
                    # 添加到Python环境列表
                    self.python_paths.append(python_exe)
                    # 保存到文件
                    self._save_python_environments()
                    # 更新下拉列表
                    self.python_env_combobox['values'] = self.python_paths
                    # 选择新添加的环境
                    self.python_env_var.set(python_exe)
                
                # 设置当前环境
                self.python_exe_path = python_exe
                self.python_env_path = os.path.dirname(python_exe)
                self.update_result_text(f"已添加并切换到Python环境: {self.python_exe_path}\n\n")
                
                # 重置检查结果和安装按钮状态
                self.check_results = {}
                self.install_btn.config(state=tk.NORMAL)
                self.has_conflicts = True
            else:
                # 在主线程中显示错误消息
                self.root.after(0, lambda: messagebox.showerror("错误", f"选择的文件不是有效的Python可执行文件: {python_exe}"))
        else:
            # 如果用户取消选择可执行文件，尝试让用户选择环境目录
            env_dir = filedialog.askdirectory(
                title="选择Python环境目录",
                initialdir=os.path.dirname(os.path.dirname(self.python_exe_path)) if self.python_exe_path else ""
            )
            
            if env_dir:
                # 尝试在选择的目录中找到Python可执行文件      
                python_exe_candidates = [
                    os.path.join(env_dir, 'python.exe'),
                    os.path.join(env_dir, 'Scripts', 'python.exe'),
                    os.path.join(env_dir, 'bin', 'python.exe')
                ]
                                    
                python_exe = None
                for candidate in python_exe_candidates:
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        python_exe = candidate
                        break
                
                if python_exe:
                    # 检查是否已经存在于列表中
                    if python_exe not in self.python_paths:
                        # 添加到Python环境列表
                        self.python_paths.append(python_exe)
                        # 保存到文件
                        self._save_python_environments()
                        # 更新下拉列表
                        self.python_env_combobox['values'] = self.python_paths
                        # 选择新添加的环境
                        self.python_env_var.set(python_exe)
                    
                    # 设置当前环境
                    self.python_exe_path = python_exe
                    self.python_env_path = env_dir
                    self.update_result_text(f"已添加并切换到Python环境: {self.python_exe_path}\n\n")
                    
                    # 重置检查结果和安装按钮状态
                    self.check_results = {}
                    self.install_btn.config(state=tk.NORMAL)
                    self.has_conflicts = True
                else:
                    # 在主线程中显示错误消息
                    self.root.after(0, lambda: messagebox.showerror("错误", f"在选择的目录中未找到有效的Python可执行文件: {env_dir}"))

    def start_installation(self):
        """开始实际安装依赖包"""
        # 检查是否选择了requirements.txt文件
        if not hasattr(self, 'dependencies') or not self.dependencies:
            messagebox.showwarning("警告", "请先选择并解析requirements.txt文件")
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
        """实际安装依赖包 - 忽略所有环境和依赖检查"""
        temp_requirements_path = None
        try:
            # 如果没有选择Python环境，使用默认Python
            if not hasattr(self, 'python_exe_path') or not self.python_exe_path:
                self.python_exe_path = 'python'
                self.update_result_text("未选择Python环境，使用默认Python...\n\n")
            

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
            mirror_url = ''
            if hasattr(self, 'selected_mirror'):
                try:
                    mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
                except:
                    pass
            if mirror_url:
                cmd.extend(['--index-url', mirror_url])
                cmd.append('--trusted-host')
                cmd.append(mirror_url.split('/')[2])  # 添加trusted-host参数
                self.update_result_text(f"使用镜像源: {self.selected_mirror} ({mirror_url})\n\n")
            
            # 使用辅助方法获取子进程参数
            kwargs = self._get_subprocess_kwargs(pipe_stdout=True)
            
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
        """清空右侧结果显示区域"""
        self.result_text.delete(1.0, tk.END)
        
    def show_pip_params(self):
        """显示pip install命令的常用参数说明"""
        # 清空结果区域
        self.clear_results()
        
        # 显示参数说明
        self.update_result_text("==== pip install 命令常用参数说明 ====\n\n")
        self.update_result_text("以下是python -m pip install命令的一些常用参数及其含义：\n\n")
        
        params = [
            ("--no-deps", "不安装包的依赖项", "示例：python.exe -m pip install package --no-deps\n当您想要安装特定包但不希望它自动安装其依赖包时使用。\n这在处理依赖冲突或特定版本控制时特别有用。\n\n"),
            ("--upgrade", "升级已安装的包", "示例：python.exe -m pip install package --upgrade\n强制升级已安装的包到最新版本。\n\n"),
            ("--force-reinstall", "强制重新安装包", "示例：python.exe -m pip install package --force-reinstall\n即使包已经安装，也会强制重新下载和安装。\n\n"),
            ("--ignore-installed", "忽略已安装的包", "示例：python.exe -m pip install package --ignore-installed\n忽略已安装的包及其依赖项，直接覆盖安装。\n\n"),
            ("--index-url", "指定包索引URL", "示例：python.exe -m pip install package --index-url=https://pypi.org/simple/\n指定从哪个包索引服务器下载包。常用于指定国内镜像源。\n\n"),
            ("--trusted-host", "添加可信主机", "示例：python.exe -m pip install package --trusted-host=pypi.org\n将指定主机标记为可信，用于HTTPS连接。\n\n"),
            ("--timeout", "设置超时时间(秒)", "示例：python.exe -m pip install package --timeout=120\n设置连接超时时间，默认为15秒。\n\n"),
            ("-v/--verbose", "详细输出模式", "示例：python.exe -m pip install package -v\n显示更详细的安装信息，有助于调试问题。\n\n"),
            ("--pre", "包含预发布版本", "示例：python.exe -m pip install package --pre\n允许安装预发布版本和开发版本。\n\n"),
            ("package==version", "指定包版本", "示例：python.exe -m pip install package==1.2.3\n安装特定版本的包。\n\n"),
            ("--user", "安装到用户目录", "示例：python.exe -m pip install package --user\n将包安装到用户主目录，无需管理员权限。\n适用于没有系统安装权限的情况。\n\n"),
            ("-e/--editable", "以开发模式安装", "示例：python.exe -m pip install -e /path/to/package\n以可编辑模式安装包，修改源代码后无需重新安装。\n常用于开发过程中测试修改。\n\n"),
            ("-r/--requirement", "从需求文件安装", "示例：python.exe -m pip install -r requirements.txt\n从指定的需求文件中批量安装多个包及其版本。\n\n"),
            ("--no-cache-dir", "不使用缓存目录", "示例：python.exe -m pip install package --no-cache-dir\n禁用pip的缓存功能，强制重新下载包。\n适用于需要获取最新包的情况。\n\n"),
            ("--dry-run", "模拟安装但不实际安装", "示例：python.exe -m pip install package --dry-run\n仅显示安装过程但不实际执行安装操作。\n常用于检查安装计划。\n\n"),
        ]
        
        for param, desc, example in params:
            self.update_result_text(f"参数: {param}\n")
            self.update_result_text(f"描述: {desc}\n")
            self.update_result_text(f"{example}")
        
        self.update_result_text("="*50 + "\n")
        self.update_result_text("提示：您可以在命令输入框中组合使用这些参数。\n")
        self.update_result_text("例如：python.exe -m pip install -r requirements.txt --user --upgrade --index-url=https://pypi.tuna.tsinghua.edu.cn/simple/")
    
    def execute_command(self):
        """执行用户输入的CMD命令"""
        # 获取命令文本
        command = self.cmd_var.get().strip()
        
        if not command:
            messagebox.showwarning("警告", "请输入要执行的命令")
            return
        
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(20)  # 设置初始进度
        
        # 在新线程中执行命令
        cmd_thread = Thread(target=self._execute_command_thread, args=(command,))
        cmd_thread.daemon = True
        cmd_thread.start()
    
    def _execute_command_thread(self, command):
        """在新线程中执行命令"""
        try:
            self.update_result_text(f"执行命令: {command}\n\n")
            
            # 获取子进程参数，使用shell=True来支持完整的CMD命令
            kwargs = self._get_subprocess_kwargs()
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE
            kwargs['shell'] = True  # 允许执行完整的CMD命令
            kwargs['encoding'] = 'utf-8'  # 明确指定编码
            kwargs['errors'] = 'replace'  # 替换无法解码的字符
            
            # 设置命令执行的工作目录为所选Python环境目录
            if self.python_env_path and os.path.isdir(self.python_env_path):
                kwargs['cwd'] = self.python_env_path
                self.update_result_text(f"命令执行目录: {self.python_env_path}\n\n")
            else:
                self.update_result_text(f"未选择Python环境，使用当前工作目录: {os.getcwd()}\n\n")
                
            # 执行命令
            process = subprocess.Popen(command, **kwargs)
            
            # 更新进度
            self.root.after(0, lambda: self.progress_var.set(40))
            
            # 使用communicate方法一次性读取所有输出，避免死锁
            stdout, stderr = process.communicate()
            
            # 更新进度
            self.root.after(0, lambda: self.progress_var.set(80))
            
            # 显示标准输出
            if stdout:
                self.update_result_text("【命令输出】\n")
                self.update_result_text(stdout)
                self.update_result_text("\n")
            
            # 显示标准错误
            if stderr:
                self.update_result_text("【错误输出】\n")
                self.update_result_text(stderr)
                self.update_result_text("\n")
            
            # 显示命令执行状态
            self.update_result_text("="*60 + "\n")
            if process.returncode == 0:
                self.update_result_text(f"命令执行成功，返回代码: {process.returncode}\n")
            else:
                self.update_result_text(f"命令执行失败，返回代码: {process.returncode}\n")
                self.root.after(0, lambda: messagebox.showerror("命令执行失败", f"命令 '{command}' 执行失败，返回代码: {process.returncode}\n请查看右侧输出窗口了解详细信息。"))
        except Exception as e:
            self.update_result_text(f"执行命令时出错: {str(e)}\n")
            self.root.after(0, lambda error=str(e): messagebox.showerror("执行错误", f"执行命令时出错: {error}"))
        finally:
            # 隐藏进度条
            self.root.after(0, lambda: self.progress_var.set(100))
            self.root.after(100, lambda: self.progress_bar.pack_forget())
    
    def copy_selected_text(self, event=None):
        """复制选中的文本到剪贴板"""
        try:
            # 获取选中的文本
            selected_text = self.result_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            # 将文本复制到剪贴板
            self.root.clipboard_clear()
            self.root.clipboard_append(selected_text)
            # 确认复制成功的反馈（可选）
            # self.update_result_text(f"已复制文本: {selected_text[:30]}...\n")
        except tk.TclError:
            # 没有选中任何文本
            pass
        return "break"  # 阻止事件继续传播
    
    def find_conflicting_libraries(self):
        """查找当前环境下第三方库之间的冲突"""
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text("开始查找环境中已安装库的冲突...\n\n")
        
        # 在新线程中执行查找，避免界面卡顿
        conflict_thread = Thread(target=self._find_conflicting_libraries_thread)
        conflict_thread.daemon = True
        conflict_thread.start()
    
    def _check_pip_check_available(self):
        """检查pip check命令是否可用"""
        try:
            # 使用辅助方法获取子进程参数
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            
            # 运行pip check --help命令来测试是否可用
            result = subprocess.run(
                [self.python_exe_path, '-m', 'pip', 'check', '--help'],
                **kwargs
            )
            
            # 如果返回码为0，说明pip check可用
            return result.returncode == 0
        except Exception:
            return False
    
    def _install_pip_check_if_needed(self):
        """安装pip check所需的包（实际上pip check是pip自带的功能，但有些版本可能需要更新pip）"""
        try:
            self.update_result_text("正在检查并安装pip check所需的组件...\n")
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            
            # 更新pip到最新版本
            result = subprocess.run(
                [self.python_exe_path, '-m', 'pip', 'install', '--upgrade', 'pip'],
                **kwargs
            )
            
            return result.returncode == 0
        except Exception:
            self.update_result_text("安装pip check组件时出错。\n")
            return False
    
    def _find_conflicting_libraries_thread(self):
        """在新线程中执行查找冲突库的操作"""
        try:
            # 先清空结果
            self.root.after(0, lambda: self.result_text.delete(1.0, tk.END))
            
            # 检查是否已选择Python环境
            if not self.python_exe_path:
                self.update_result_text("错误: 请先选择Python环境!\n")
                self.root.after(0, self.progress_bar.pack_forget)
                return
            
            # 更新进度
            self.root.after(0, lambda: self.progress_var.set(10))
            
            # 使用pip check命令检测冲突
            self.update_result_text("正在使用pip check命令检测库冲突...\n")
            
            # 检查pip check是否可用
            pip_check_available = self._check_pip_check_available()
            
            # 如果pip check不可用，尝试安装所需组件
            if not pip_check_available:
                self.update_result_text("pip check命令不可用，尝试安装所需组件...\n")
                pip_check_available = self._install_pip_check_if_needed()
                
                # 如果安装后仍然不可用，提示用户
                if not pip_check_available:
                    self.update_result_text("无法使用pip check，请手动检查您的Python环境。\n\n")
                    return
            
            # 使用pip check检测冲突
            conflicts_from_pip_check = self._run_pip_check()
            
            # 更新进度
            self.root.after(0, lambda: self.progress_var.set(90))
            
            # 显示pip check的结果
            if conflicts_from_pip_check:
                self.update_result_text(f"pip check 发现 {len(conflicts_from_pip_check)} 个库冲突问题:\n\n")
                
                for i, conflict in enumerate(conflicts_from_pip_check, 1):
                    # 获取冲突消息
                    message = conflict['message'] if isinstance(conflict, dict) else conflict
                    self.update_result_text(f"{i}. {message}\n")
                    
                    # 为每个冲突提供建议
                    suggestion = self._get_conflict_suggestion(message)
                    if suggestion:
                        # 处理建议中的换行符
                        suggestion_lines = suggestion.split('\n')
                        for line in suggestion_lines:
                            if line.strip():
                                self.update_result_text(f"{line}\n")
                    
                    self.update_result_text("\n")
            else:
                self.update_result_text("pip check未发现明显的库冲突问题。\n\n")
                self.update_result_text("环境中的库依赖关系良好！\n")
                
            self.update_result_text("冲突检查完成！\n")
            
        except Exception as e:
            self.update_result_text(f"查找冲突时出错: {str(e)}\n")
            self.update_result_text("请检查您的Python环境并尝试重新运行。\n\n")
        finally:
            # 在主线程中隐藏进度条
            self.root.after(0, self.progress_bar.pack_forget)
    
    def _run_pip_check(self):
        """运行pip check命令并解析结果，提供结构化的冲突信息"""
        try:
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            
            self.update_result_text("正在执行pip check命令...\n")
            result = subprocess.run(
                [self.python_exe_path, '-m', 'pip', 'check'],
                **kwargs
            )
            
            conflicts = []
            
            # pip check在发现冲突时会返回非零退出码
            if result.returncode != 0:
                # 解析错误输出
                error_output = result.stdout + result.stderr
                
                # 处理输出中的每一行
                for line in error_output.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('No broken requirements'):
                        # 创建结构化的冲突信息，便于后续处理
                        conflict_info = {
                            'message': line,
                            'package': None
                        }
                        
                        # 尝试从冲突消息中提取包名（通常是第一个单词）
                        words = line.split()
                        if words:
                            conflict_info['package'] = words[0]
                        
                        conflicts.append(conflict_info)
            
            return conflicts
        except Exception as e:
            self.update_result_text(f"运行pip check时出错: {str(e)}\n")
            return []
    
    def _get_conflict_suggestion(self, conflict_message):
        """为冲突提供解决建议"""
        try:
            # 根据冲突消息提供建议
            if 'requires' in conflict_message.lower():
                # 解析冲突的包名
                words = conflict_message.split()
                package_name = None
                
                # 尝试找到包名（通常是第一个单词）
                if words:
                    package_name = words[0]
                    
                if package_name:
                    return f"使用 pipdeptree 分析'{package_name}'的依赖关系: python -m pipdeptree --reverse --packages {package_name}\n 根据结果,建议升级或降级相关包版本,达到平衡进行排错,如果一个太老,一个太新,就那只能取舍"
                
            return "使用 pipdeptree 分析依赖关系: python -m pipdeptree --reverse --packages 包名\n根据树形结构分析结果确定冲突来源和解决方案"
        except:
            return None
  
    def _get_all_installed_packages(self):
        """获取当前Python环境中所有已安装的包"""
        try:
            # 使用pip list命令获取已安装包
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            
            result = subprocess.run(
                [self.python_exe_path, '-m', 'pip', 'list', '--format=columns'],
                **kwargs
            )
            
            packages = []
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[2:]  # 跳过表头
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 1:
                            packages.append(parts[0])
            
            return packages
        except Exception:
            return []
            
    def _get_all_installed_packages_with_versions(self):
        """一次性获取所有已安装包及其版本信息（性能优化）"""
        try:
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            
            # 尝试使用JSON格式获取包信息，这比多次调用pip show要快得多
            try:
                result = subprocess.run(
                    [self.python_exe_path, '-m', 'pip', 'list', '--format=json'],
                    **kwargs
                )
                
                if result.returncode == 0 and result.stdout:
                    import json
                    packages_data = json.loads(result.stdout)
                    return {pkg['name']: pkg['version'] for pkg in packages_data}
            except:
                # JSON格式不可用，回退到普通格式
                pass
            
            # 回退方法：使用普通格式解析
            result = subprocess.run(
                [self.python_exe_path, '-m', 'pip', 'list', '--format=columns'],
                **kwargs
            )
            
            packages = {}
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[2:]  # 跳过表头
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 2:
                            packages[parts[0]] = parts[1]
            
            return packages
        except Exception:
            return {}
        
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
            added, removed, changed = self._compare_environment_packages(packages1, packages2, file1_path, file2_path)
            
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
        
    def _compare_environment_packages(self, packages1, packages2, file1_path=None, file2_path=None):
        """比较两个环境的包信息，返回差异"""
        # 添加日志，明确标识哪个文件对应哪个包列表
        if file1_path and file2_path:
            self.update_result_text(f"正在比较包信息: 文件1({os.path.basename(file1_path)}) vs 文件2({os.path.basename(file2_path)})\n")
        
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
            try:
                # 保存当前的滚动位置
                current_pos = self.result_text.yview()[1]
                # 插入新文本
                self.result_text.insert(tk.END, text)
                # 如果滚动条已经在底部，则自动滚动到底部
                if current_pos > 0.95:  # 如果滚动条接近底部
                    self.result_text.see(tk.END)
            except tk.TclError:
                # 如果文本框已被销毁，忽略错误
                pass
        
        # 检查是否在主线程中，如果不是则通过after在主线程中执行
        if self.root._windowingsystem is not None and self.root.winfo_exists():
            self.root.after(0, _update_text)
        else:
            # 如果窗口已关闭，不进行更新
            pass
            
    def search_library_exact(self):
        """精确查找第三方库并获取版本列表"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 检查是否输入了库名称
        lib_name = self.lib_name_var.get().strip()
        if not lib_name:
            messagebox.showwarning("警告", "请输入要查找的库名称")
            return
        
        # 将使用过的库名添加到历史记录中（如果不存在）
        self._add_to_lib_history(lib_name)
            
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text(f"正在精确搜索库 {lib_name} ...\n\n")
        
        # 在新线程中执行精确搜索
        search_thread = Thread(target=self._search_library_thread, args=(lib_name,))
        search_thread.daemon = True
        search_thread.start()
        
    def search_library_local(self):
        """从本地环境中模糊查找第三方库"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 创建自定义对话框类，支持设置图标
        class CustomAskStringDialog(simpledialog._QueryString):
            def __init__(self, parent, title, prompt, icon_path=None):
                self.icon_path = icon_path
                super().__init__(parent, title, prompt)
            
            def body(self, master):
                body = super().body(master)
                # 设置对话框图标
                if self.icon_path and os.path.exists(self.icon_path):
                    try:
                        self.iconbitmap(self.icon_path)
                    except:
                        # 图标设置失败不影响程序运行
                        pass
                return body
        
        # 弹出输入框让用户输入模糊查找的字符，使用自定义对话框并设置图标
        icon_path = "favicon.ico"
        if not os.path.exists(icon_path):
            # 如果相对路径不存在，使用程序所在目录
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon.ico")
            
        dialog = CustomAskStringDialog(
            self.root, 
            "模糊查找", 
            "请输入要模糊查找的库名称字符：",
            icon_path
        )
        search_term = dialog.result
        
        # 如果用户点击了取消按钮
        if search_term is None:
            return
            
        # 去除首尾空格
        search_term = search_term.strip()
        
        # 如果输入为空
        if not search_term:
            messagebox.showwarning("警告", "搜索字符不能为空")
            return
            
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text(f"正在本地环境中模糊查找匹配 '{search_term}' 的库...\n\n")
        
        # 在新线程中执行本地模糊搜索
        search_thread = Thread(target=self._search_library_local_thread, args=(search_term,))
        search_thread.daemon = True
        search_thread.start()
        
    def _search_library_local_thread(self, search_term):
        """在新线程中从本地环境模糊查找库"""
        try:
            # Windows系统专用实现
            # 使用安全的方式执行命令，不使用shell=True
            # 1. 首先获取所有已安装的包列表
            cmd_pip_list = [self.python_exe_path, '-m', 'pip', 'list', '--format=columns']
            kwargs_pip_list = self._get_run_subprocess_kwargs(capture_output=True, timeout=Config.DEFAULT_TIMEOUT)
            
            # 执行pip list命令
            result_pip_list = subprocess.run(cmd_pip_list, **kwargs_pip_list)
            
            # 2. 手动过滤结果以模拟findstr功能
            if result_pip_list.returncode == 0:
                # 模拟命令执行结果，设置stdout和returncode
                class MockResult:
                    def __init__(self, stdout, returncode):
                        self.stdout = stdout
                        self.returncode = returncode
                        self.stderr = ''
                
                # 过滤包含搜索词的行
                output_lines = result_pip_list.stdout.strip().split('\n')
                filtered_lines = []
                for line in output_lines:
                    if search_term.lower() in line.lower():
                        filtered_lines.append(line)
                
                # 创建模拟结果对象
                filtered_stdout = '\n'.join(filtered_lines)
                result = MockResult(filtered_stdout, 0 if filtered_lines else 1)
            else:
                result = result_pip_list
            
            # 处理结果
            if result.returncode == 0:
                # 命令执行成功，有匹配结果
                output_lines = result.stdout.strip().split('\n')
                self.update_result_text(f"在本地环境中找到 {len(output_lines)} 个匹配的库:\n\n")
                
                # 显示匹配的库和版本
                for line in output_lines:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        lib_name = parts[0]
                        version = parts[1]
                        self.update_result_text(f"{lib_name}  (版本: {version})\n")
                    else:
                        self.update_result_text(f"{line}\n")
                
                self.update_result_text("\n")
            else:
                # 命令执行失败，可能没有匹配结果
                self.update_result_text(f"未在本地环境中找到匹配 '{search_term}' 的库\n\n")
            
            if result.stderr:
                # 如果有错误输出，也显示出来
                self.update_result_text(f"警告: {result.stderr}\n\n")
        except Exception as e:
            self.update_result_text(f"本地查找库时出错: {str(e)}\n\n")
        finally:
            # 隐藏进度条
            self.root.after(0, lambda: self.progress_bar.pack_forget())
        
    def _search_library_thread(self, lib_name):
        """在新线程中查找库信息和版本列表"""
        try:
            # 先检查当前环境是否已安装该库
            self._check_installed_library(lib_name)
            
            # 然后查找该库的可用版本列表
            self._get_library_versions(lib_name)
        except Exception as e:
            self.update_result_text(f"查找库信息时出错: {str(e)}\n")
        finally:
            # 隐藏进度条
            self.root.after(0, lambda: self.progress_bar.pack_forget())
            
    def _check_installed_library(self, lib_name):
        """检查当前环境是否已安装指定库"""
        try:
            # 使用pip show命令检查库是否已安装
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            
            result = subprocess.run(
                [self.python_exe_path, '-m', 'pip', 'show', lib_name],
                **kwargs
            )
            
            if result.returncode == 0:
                # 库已安装，解析输出获取版本信息
                output_lines = result.stdout.strip().split('\n')
                version_line = next((line for line in output_lines if line.startswith('Version: ')), None)
                if version_line:
                    version = version_line.split('Version: ')[1]
                    self.update_result_text(f"库 {lib_name} 已安装在当前环境中，版本: {version}\n\n")
                else:
                    self.update_result_text(f"库 {lib_name} 已安装在当前环境中\n\n")
            else:
                self.update_result_text(f"库 {lib_name} 未安装在当前环境中\n\n")
        except Exception as e:
            self.update_result_text(f"检查库安装状态时出错: {str(e)}\n")
            
    def _get_library_versions(self, lib_name):
        """获取指定库的可用版本列表"""
        try:
            # 使用pip index versions命令获取版本列表（需要pip 21.2+）
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            
            # 构建命令
            command = [self.python_exe_path, '-m', 'pip', 'index', 'versions', lib_name]
            
            # 添加镜像源参数（如果有）
            mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
            if mirror_url:
                command.extend(['--index-url', mirror_url])
                command.append('--trusted-host')
                command.append(mirror_url.split('/')[2])  # 添加trusted-host参数
            
            result = subprocess.run(command, **kwargs)
            
            # 处理输出，提取版本信息
            if result.returncode == 0:
                output_lines = result.stdout.strip().split('\n')
                version_line = next((line for line in output_lines if 'Available versions:' in line), None)
                
                if version_line:
                    # 提取版本信息
                    versions_text = version_line.split('Available versions: ')[1]
                    versions = [v.strip() for v in versions_text.split(',')]
                    
                    self.update_result_text(f"库 {lib_name} 的可用版本: {', '.join(versions)}\n\n")
                    
                    # 在主线程中更新版本下拉框
                    def update_version_combobox(versions_list):
                        self.version_combobox['values'] = versions_list
                        if versions_list:
                            self.version_combobox.current(0)
                    
                    self.root.after(0, update_version_combobox, versions)
                else:
                    self.update_result_text(f"未找到库 {lib_name} 的版本信息\n\n")
            else:
                # 如果pip index versions命令失败，尝试使用pip install --dry-run来获取版本信息
                self.update_result_text(f"尝试使用替代方法获取版本信息...\n")
                self._get_library_versions_alt(lib_name)
        except Exception as e:
            self.update_result_text(f"获取版本列表时出错: {str(e)}\n")
            # 尝试使用替代方法
            self._get_library_versions_alt(lib_name)
            
    def _get_library_versions_alt(self, lib_name):
        """获取版本列表的替代方法"""
        try:
            # 使用pip install --dry-run命令获取版本信息
            kwargs = {
                'capture_output': True,
                'text': True
            }
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            
            # 构建命令
            command = [self.python_exe_path, '-m', 'pip', 'install', f'{lib_name}==*', '--dry-run']
            
            # 添加镜像源参数（如果有）
            mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
            if mirror_url:
                command.extend(['--index-url', mirror_url])
                command.append('--trusted-host')
                command.append(mirror_url.split('/')[2])  # 添加trusted-host参数
            
            result = subprocess.run(command, **kwargs)
            
            # 解析错误输出中的版本信息（因为==*是无效的版本号）
            if result.returncode != 0:
                # 从stderr中提取有效的版本信息
                error_output = result.stderr
                if 'versions: ' in error_output:
                    versions_part = error_output.split('versions: ')[1].split('\n')[0]
                    versions = [v.strip() for v in versions_part.split(',')]
                    
                    self.update_result_text(f"库 {lib_name} 的可用版本: {', '.join(versions)}\n\n")
                    
                    # 在主线程中更新版本下拉框
                    def update_version_combobox_alt(versions_list):
                        self.version_combobox['values'] = versions_list
                        if versions_list:
                            self.version_combobox.current(0)
                    
                    self.root.after(0, update_version_combobox_alt, versions)
        except Exception as e:
            self.update_result_text(f"使用替代方法获取版本列表时出错: {str(e)}\n")
            
    def install_library(self):
        """安装指定版本的第三方库"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 检查是否输入了库名称
        lib_name = self.lib_name_var.get().strip()
        if not lib_name:
            messagebox.showwarning("警告", "请输入要安装的库名称")
            return
        
        # 将使用过的库名添加到历史记录中（如果不存在）
        self._add_to_lib_history(lib_name)
            
        # 获取选择的版本
        version = self.version_var.get().strip()
        
        # 构建安装命令
        if version:
            install_target = f"{lib_name}=={version}"
        else:
            install_target = lib_name
            
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text(f"正在安装库 {install_target}...\n\n")
        
        # 在新线程中执行安装
        install_thread = Thread(target=self._install_library_thread, args=(install_target,))
        install_thread.daemon = True
        install_thread.start()
        
    def _install_library_thread(self, install_target):
        """在新线程中安装库"""
        try:
            # 构建pip install命令
            command = [self.python_exe_path, '-m', 'pip', 'install', install_target]
            
            # 添加镜像源参数
            mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
            if mirror_url:
                command.extend(['-i', mirror_url])
                command.append('--trusted-host')
                command.append(mirror_url.split('/')[2])  # 添加trusted-host参数
            
            # 使用辅助方法获取子进程参数
            kwargs = self._get_subprocess_kwargs()
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE
            
            process = subprocess.Popen(command, **kwargs)
            
            # 实时显示输出
            for line in process.stdout:
                self.update_result_text(line)
                
            for line in process.stderr:
                self.update_result_text(line)
            
            # 等待进程完成
            process.wait()
            
            if process.returncode == 0:
                self.root.after(0, lambda: messagebox.showinfo("安装成功", f"库 {install_target} 安装成功!"))
            else:
                self.root.after(0, lambda: messagebox.showerror("安装失败", f"库 {install_target} 安装失败!"))
        except Exception as e:
            self.update_result_text(f"安装库时出错: {str(e)}\n")
        finally:
            # 隐藏进度条
            self.root.after(0, lambda: self.progress_bar.pack_forget())
            
    def uninstall_library(self):
        """删除指定的第三方库"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 检查是否输入了库名称
        lib_name = self.lib_name_var.get().strip()
        if not lib_name:
            messagebox.showwarning("警告", "请输入要删除的库名称")
            return
        
        # 将使用过的库名添加到历史记录中（如果不存在）
        self._add_to_lib_history(lib_name)
            
        # 确认删除
        if not messagebox.askyesno("确认删除", f"确定要删除库 {lib_name} 吗？"):
            return
            
        # 显示进度条
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_var.set(0)
        self.update_result_text(f"正在删除库 {lib_name}...\n\n")
        
        # 在新线程中执行删除
        uninstall_thread = Thread(target=self._uninstall_library_thread, args=(lib_name,))
        uninstall_thread.daemon = True
        uninstall_thread.start()

    def install_whl_file(self):
        """安装whl预编译文件"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 打开文件对话框让用户选择whl文件
        whl_file = filedialog.askopenfilename(
            title="选择whl预编译文件",
            filetypes=[("Wheel文件", "*.whl"), ("所有文件", "*")]
        )
        
        if whl_file:
            # 显示进度条
            self.progress_bar.pack(fill=tk.X, pady=5)
            self.progress_var.set(0)
            # 在新线程中执行安装
            install_thread = Thread(target=self._install_whl_thread, args=(whl_file,))
            install_thread.daemon = True
            install_thread.start()
    
    def _install_whl_thread(self, whl_file):
        """在新线程中安装whl文件"""
        try:
            # 构建pip install命令
            command = [self.python_exe_path, '-m', 'pip', 'install', whl_file]
            
            # 添加镜像源参数
            mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
            if mirror_url:
                command.extend(['-i', mirror_url])
                command.append('--trusted-host')
                command.append(mirror_url.split('/')[2])  # 添加trusted-host参数
            
            # 使用辅助方法获取子进程参数，但明确设置编码以确保正确显示错误信息
            kwargs = self._get_subprocess_kwargs()
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE
            kwargs['encoding'] = 'utf-8'  # 明确指定编码
            kwargs['errors'] = 'replace'  # 替换无法解码的字符
            
            self.update_result_text(f"开始安装whl文件: {whl_file}\n\n")
            
            process = subprocess.Popen(command, **kwargs)
            
            # 使用communicate方法一次性读取所有输出，避免死锁
            stdout, stderr = process.communicate()
            
            # 显示标准输出
            if stdout:
                self.update_result_text(stdout)
            
            # 显示标准错误（错误信息通常在这里）
            if stderr:
                self.update_result_text("\n【错误输出】\n")
                self.update_result_text(stderr)
            
            if process.returncode == 0:
                self.root.after(0, lambda: messagebox.showinfo("安装成功", f"whl文件 {os.path.basename(whl_file)} 安装成功!"))
            else:
                # 在错误提示中包含更详细的信息
                error_msg = f"whl文件 {os.path.basename(whl_file)} 安装失败!\n\n" \
                           f"请查看右侧输出窗口了解详细的错误信息。\n" \
                           f"常见原因包括：编译环境缺失、依赖不满足、Python版本不兼容等。"
                self.root.after(0, lambda: messagebox.showerror("安装失败", error_msg))
        except Exception as e:
            self.update_result_text(f"安装whl文件时出错: {str(e)}\n")
        finally:
            # 隐藏进度条
            self.root.after(0, lambda: self.progress_bar.pack_forget())
            
    def install_source_code(self):
        """从源代码目录编译安装Python库"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            messagebox.showwarning("警告", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 检查编译环境
        if not self._check_build_environment():
            return
            
        # 打开文件夹对话框让用户选择源代码目录
        source_dir = filedialog.askdirectory(
            title="选择Python源代码目录"
        )
        
        if source_dir:
            # 在新线程中执行安装
            install_thread = Thread(target=self._install_source_thread, args=(source_dir,))
            install_thread.daemon = True
            install_thread.start()
            
    def _check_build_environment(self):
        """检查编译环境是否满足要求"""
        missing_components = []
        
        # 检查Python构建库
        missing_libraries = self._check_python_build_libraries()
        if missing_libraries:
            missing_components.extend([f"Python库: {lib}" for lib in missing_libraries])
            
        # 检查MSVC工具
        missing_tools = self._check_msvc_tools()
        if missing_tools:
            missing_components.extend(missing_tools)
            
        # 如果有缺失的组件，显示警告
        if missing_components:
            warning_msg = "编译环境检测失败，缺少以下组件：\n\n"
            warning_msg += "\n".join(missing_components)
            warning_msg += "\n\n请安装这些组件后再尝试编译安装。"
            messagebox.showwarning("编译环境不完整", warning_msg)
            return False
            
        self.update_result_text("编译环境检测通过，可以开始编译安装。\n\n")
        return True
        
    def _check_python_build_libraries(self):
        """检查setuptools和wheel库是否已安装"""
        missing_libraries = []
        required_libraries = ["setuptools", "wheel"]
        
        for lib in required_libraries:
            try:
                kwargs = self._get_subprocess_kwargs(capture_output=True)
                result = subprocess.run(
                    [self.python_exe_path, '-m', 'pip', 'show', lib],
                    **kwargs
                )
                
                if result.returncode != 0:
                    missing_libraries.append(lib)
            except Exception:
                missing_libraries.append(lib)
                
        return missing_libraries
        
    def _check_msvc_tools(self):
        """检查系统是否安装了MSVC的C++桌面开发、Windows SDK和C++ CMake工具"""
        missing_tools = []
        
        # 使用vswhere工具检测Visual Studio组件
        try:
            # 尝试直接检测编译器是否可用
            if self._check_cl_compiler_available():
                # 如果编译器可用，跳过其他详细检测
                return missing_tools
                
            # 查找vswhere工具
            vswhere_path = None
            
            # 检查常见的vswhere路径
            program_files = os.environ.get('ProgramFiles(x86)', 'C:\Program Files (x86)')
            possible_paths = [
                os.path.join(program_files, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe'),
                os.path.join(os.environ.get('ProgramFiles', 'C:\Program Files'), 'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    vswhere_path = path
                    break
            
            # 如果找不到vswhere，尝试通过where命令查找
            if not vswhere_path:
                kwargs = self._get_subprocess_kwargs(capture_output=True)
                result = subprocess.run(['where', 'vswhere.exe'], **kwargs)
                if result.returncode == 0 and result.stdout:
                    vswhere_path = result.stdout.strip().split('\n')[0]
                    
            # 如果找到了vswhere，使用它检测组件
            vs_components_found = False
            if vswhere_path:
                # 扩展版本范围，支持VS2017 (15.0), VS2019 (16.0), VS2022 (17.0)
                version_range = '15.0,16.0,17.0'
                
                kwargs = self._get_subprocess_kwargs(capture_output=True)
                
                # 检测C++桌面开发工作负载或编译器组件
                cpp_results = []
                # 尝试不同的C++开发相关组件ID
                cpp_components = [
                    'Microsoft.VisualStudio.Workload.NativeDesktop',  # C++桌面开发
                    'Microsoft.VisualStudio.Workload.VCTools',         # C++生成工具
                    'Microsoft.VisualStudio.Component.VC.Tools.x86.x64'  # C++编译器
                ]
                
                for comp_id in cpp_components:
                    result = subprocess.run(
                        [vswhere_path, '-products', '*', '-requires', comp_id, '-version', version_range, '-latest', '-property', 'installationPath'],
                        **kwargs
                    )
                    if result.stdout.strip():
                        cpp_results.append(True)
                        break
                    
                if not any(cpp_results):
                    missing_tools.append("MSVC C++开发工具")
                else:
                    vs_components_found = True
                    
                # 检测Windows SDK (允许没有SDK，因为某些库可能不需要)
                # 检测CMake工具 (允许没有CMake，因为某些库可能不需要)
                    
            # 如果vswhere没有检测到，但编译器路径检测到，也认为环境可用
            if not vs_components_found:
                # 扩展编译器路径检测，包括更多VS版本和路径模式
                compiler_found = self._check_compiler_paths()
                
                if not compiler_found:
                    # 提供忽略检测的选项
                    if messagebox.askyesno("检测提示", "未检测到MSVC编译器，但您确认已安装。\n\n是否跳过环境检测继续编译安装？"):
                        return []  # 用户确认已安装，返回空列表表示环境满足
                    missing_tools.append("未检测到MSVC编译器")
                    missing_tools.append("请确认已安装Visual Studio或Build Tools及C++开发组件")
                    
        except Exception as e:
            missing_tools.append(f"检测MSVC工具时出错: {str(e)}")
            # 出错时也提供跳过检测的选项
            if messagebox.askyesno("检测错误", f"检测MSVC工具时出错: {str(e)}\n\n是否跳过环境检测继续编译安装？"):
                return []
            
        return missing_tools
        
    def _check_cl_compiler_available(self):
        """直接检查cl.exe编译器是否可用"""
        try:
            kwargs = self._get_subprocess_kwargs(capture_output=True)
            # 尝试运行cl.exe /?命令
            result = subprocess.run(['cl.exe', '/?'], **kwargs)
            # 如果返回码为0或1(通常cl.exe /?返回1但有输出)且有输出，表示编译器可用
            return result.returncode in [0, 1] and result.stdout
        except Exception:
            return False
            
    def _check_compiler_paths(self):
        """检查更多可能的编译器路径"""
        program_files = os.environ.get('ProgramFiles(x86)', 'C:\Program Files (x86)')
        program_files64 = os.environ.get('ProgramFiles', 'C:\Program Files')
        
        # 扩展编译器路径模式，包括更多VS版本和架构
        compiler_path_patterns = [
            # VS2022路径
            os.path.join(program_files, 'Microsoft Visual Studio', '2022', '*', 'VC', 'Tools', 'MSVC', '*', 'bin', 'Hostx64', 'x64', 'cl.exe'),
            os.path.join(program_files, 'Microsoft Visual Studio', '2022', '*', 'VC', 'Tools', 'MSVC', '*', 'bin', 'Hostx86', 'x86', 'cl.exe'),
            # VS2019路径
            os.path.join(program_files, 'Microsoft Visual Studio', '2019', '*', 'VC', 'Tools', 'MSVC', '*', 'bin', 'Hostx64', 'x64', 'cl.exe'),
            os.path.join(program_files, 'Microsoft Visual Studio', '2019', '*', 'VC', 'Tools', 'MSVC', '*', 'bin', 'Hostx86', 'x86', 'cl.exe'),
            # VS2017路径
            os.path.join(program_files, 'Microsoft Visual Studio', '2017', '*', 'VC', 'Tools', 'MSVC', '*', 'bin', 'Hostx64', 'x64', 'cl.exe'),
            os.path.join(program_files, 'Microsoft Visual Studio', '2017', '*', 'VC', 'Tools', 'MSVC', '*', 'bin', 'Hostx86', 'x86', 'cl.exe'),
            # 64位Program Files下的路径
            os.path.join(program_files64, 'Microsoft Visual Studio', '*', '*', 'VC', 'Tools', 'MSVC', '*', 'bin', 'Hostx64', 'x64', 'cl.exe'),
            # 直接在PATH中的cl.exe
            'cl.exe'  # 如果在PATH中可以直接找到
        ]
        
        # 检查注册表中的编译器路径
        try:
            import winreg
            # 尝试从注册表获取Visual Studio安装路径
            vs_reg_paths = [
                r'SOFTWARE\Microsoft\VisualStudio\SxS\VS7',
                r'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\SxS\VS7'
            ]
            
            for reg_path in vs_reg_paths:
                try:
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
                    i = 0
                    while True:
                        try:
                            version, path, _ = winreg.EnumValue(key, i)
                            if path and os.path.exists(os.path.join(path, 'VC', 'Tools', 'MSVC')):
                                # 找到了VS安装路径，认为编译器可能存在
                                return True
                            i += 1
                        except OSError:
                            break
                except Exception:
                    pass
        except Exception:
            pass
            
        # 检查文件系统中的路径
        import glob
        for pattern in compiler_path_patterns:
            try:
                if pattern == 'cl.exe':
                    # 直接检查PATH中的cl.exe
                    kwargs = self._get_subprocess_kwargs(capture_output=True)
                    result = subprocess.run(['where', 'cl.exe'], **kwargs)
                    if result.returncode == 0 and result.stdout:
                        return True
                else:
                    # 检查通配符路径
                    if glob.glob(pattern):
                        return True
            except Exception:
                continue
                
        return False
    
    def _install_source_thread(self, source_dir):
        """在新线程中从源代码安装库"""
        try:
            # 构建pip install命令
            command = [self.python_exe_path, '-m', 'pip', 'install', '.']
            
            # 添加镜像源参数
            mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
            if mirror_url:
                command.extend(['-i', mirror_url])
                command.append('--trusted-host')
                command.append(mirror_url.split('/')[2])  # 添加trusted-host参数
            
            # 使用辅助方法获取子进程参数，但明确设置编码以确保正确显示错误信息
            kwargs = self._get_subprocess_kwargs()
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE
            kwargs['cwd'] = source_dir  # 设置工作目录为源代码目录
            kwargs['encoding'] = 'utf-8'  # 明确指定编码
            kwargs['errors'] = 'replace'  # 替换无法解码的字符
            
            self.update_result_text(f"开始从源代码目录编译安装: {source_dir}\n\n")
            
            process = subprocess.Popen(command, **kwargs)
            
            # 使用communicate方法一次性读取所有输出，避免死锁
            stdout, stderr = process.communicate()
            
            # 显示标准输出
            if stdout:
                self.update_result_text(stdout)
            
            # 显示标准错误（错误信息通常在这里）
            if stderr:
                self.update_result_text("\n【错误输出】\n")
                self.update_result_text(stderr)
            
            if process.returncode == 0:
                self.root.after(0, lambda: messagebox.showinfo("安装成功", f"从源代码目录 {os.path.basename(source_dir)} 安装成功!"))
            else:
                # 在错误提示中包含更详细的信息
                error_msg = f"从源代码目录 {os.path.basename(source_dir)} 安装失败!\n\n" \
                           f"请查看右侧输出窗口了解详细的错误信息。\n" \
                           f"常见原因包括：编译环境缺失(MSVC)、依赖不满足、Python版本不兼容等。\n" \
                           f"您可以尝试先安装编译依赖，或检查MSVC是否正确安装。"
                self.root.after(0, lambda: messagebox.showerror("安装失败", error_msg))
        except Exception as e:
            self.update_result_text(f"从源代码安装时出错: {str(e)}\n")
        finally:
            # 隐藏进度条
            self.root.after(0, lambda: self.progress_bar.pack_forget())
        uninstall_thread.daemon = True
        uninstall_thread.start()
        
    def _uninstall_library_thread(self, lib_name):
        """在新线程中删除库"""
        try:
            # 构建pip uninstall命令
            command = [self.python_exe_path, '-m', 'pip', 'uninstall', lib_name, '-y']
            
            # 使用辅助方法获取子进程参数
            kwargs = self._get_subprocess_kwargs()
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE
            
            process = subprocess.Popen(command, **kwargs)
            
            # 实时显示输出
            for line in process.stdout:
                self.update_result_text(line)
                
            for line in process.stderr:
                self.update_result_text(line)
            
            # 等待进程完成
            process.wait()
            
            if process.returncode == 0:
                self.root.after(0, lambda: messagebox.showinfo("删除成功", f"库 {lib_name} 删除成功!"))
            else:
                self.root.after(0, lambda: messagebox.showerror("删除失败", f"库 {lib_name} 删除失败!"))
        except Exception as e:
            self.update_result_text(f"删除库时出错: {str(e)}\n")
        finally:
            # 隐藏进度条
            self.root.after(0, lambda: self.progress_bar.pack_forget())
            
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
        
    def _add_to_lib_history(self, lib_name):
        """将库名称添加到历史记录中"""
        # 避免重复添加
        if lib_name and lib_name not in self.lib_history:
            self.lib_history.append(lib_name)
            # 限制历史记录的最大数量
            if len(self.lib_history) > Config.MAX_HISTORY_ITEMS:
                self.lib_history = self.lib_history[-Config.MAX_HISTORY_ITEMS:]
            # 更新下拉框的值列表
            self.lib_name_combobox['values'] = self.lib_history
        
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
                
                # 使用专门用于subprocess.run的辅助方法，包含timeout支持
                kwargs = self._get_run_subprocess_kwargs(capture_output=True, timeout=Config.DEFAULT_TIMEOUT)
                # 自定义stdout和stderr处理
                kwargs['stdout'] = subprocess.PIPE
                kwargs['stderr'] = subprocess.STDOUT
                kwargs.pop('capture_output', None)  # 移除capture_output，因为我们明确设置了stdout和stderr
                
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
                self.update_result_text(f"{mirror_name} 测试超时（>{Config.DEFAULT_TIMEOUT}秒）\n\n")
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
    # 实现单例应用功能（仅支持Windows平台）
    import atexit
    import sys
    
    # 单例实现 - 仅Windows平台
    is_single_instance = True
    lock_file = None
    
    try:
        # Windows平台实现
        import msvcrt
        lock_file = open(Config.LOCK_FILE_PATH, 'w')
        # 使用非阻塞锁
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except:
            is_single_instance = False
        
        # 确保程序退出时释放锁文件
        def release_lock():
            try:
                if lock_file:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    lock_file.close()
                if os.path.exists(Config.LOCK_FILE_PATH):
                    os.remove(Config.LOCK_FILE_PATH)
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