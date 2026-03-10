import customtkinter as ctk
import os
import threading
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser

# 加载环境变量
load_dotenv()

# 初始化 GUI 主窗口
ctk.set_appearance_mode("System")  # "System", "Dark", "Light"
ctk.set_default_color_theme("blue")  # Themes: "blue" (standard), "green", "dark-blue"

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("小红书爆款文案生成器")
        self.geometry("600x500")

        # UI 布局
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 标题栏
        self.title_label = ctk.CTkLabel(self, text="🌟 小红书爆款文案生成器 🌟", font=ctk.CTkFont(size=24, weight="bold"))
        self.title_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        # 输入区域
        self.input_frame = ctk.CTkFrame(self)
        self.input_frame.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        self.input_frame.grid_columnconfigure(1, weight=1)

        self.topic_label = ctk.CTkLabel(self.input_frame, text="主题名称：", font=ctk.CTkFont(size=14))
        self.topic_label.grid(row=0, column=0, padx=10, pady=10)

        self.topic_entry = ctk.CTkEntry(self.input_frame, placeholder_text="例如：夏日防晒、周末看展...", width=300)
        self.topic_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

        self.generate_button = ctk.CTkButton(self.input_frame, text="✨ 一键生成", command=self.generate_titles)
        self.generate_button.grid(row=0, column=2, padx=10, pady=10)

        # 结果显示区域
        self.result_textbox = ctk.CTkTextbox(self, font=ctk.CTkFont(size=14))
        self.result_textbox.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="nsew")
        self.result_textbox.insert("0.0", "文案结果将显示在这里...")
        self.result_textbox.configure(state="disabled")
        
        # 初始化 LangChain 模型
        self.init_model()

    def init_model(self):
        try:
            # 检查是否有 API Key
            api_key = os.getenv("MOONSHOT_API_KEY")
            if not api_key or "your_" in api_key:
                self.show_error("请先在 .env 文件中配置 MOONSHOT_API_KEY！")
                self.generate_button.configure(state="disabled")
                return

            self.model = ChatOpenAI(
                api_key=api_key,
                base_url="https://api.moonshot.cn/v1",
                model="moonshot-v1-8k",
                temperature=0.7
            )

            self.prompt = ChatPromptTemplate.from_messages([
                ("system", "你是一个精通全网爆款逻辑的小红书文案专家。请根据用户提供的主题，自动思考用户的受众痛点，并生成三个极具网感、带有恰当emoji、能引起强烈共鸣或好奇心的小红书爆款标题。每个标题之间请换行，直接输出文本即可不用做别的废话。"),
                ("user", "主题：{topic}")
            ])

            self.chain = self.prompt | self.model | StrOutputParser()
        except Exception as e:
            self.show_error(f"模型初始化失败：{str(e)}")

    def show_error(self, message):
        self.result_textbox.configure(state="normal")
        self.result_textbox.delete("0.0", "end")
        self.result_textbox.insert("0.0", f"🚨 错误：\n{message}")
        self.result_textbox.configure(state="disabled")

    def update_result(self, message):
        self.result_textbox.configure(state="normal")
        self.result_textbox.delete("0.0", "end")
        self.result_textbox.insert("0.0", message)
        self.result_textbox.configure(state="disabled")

    def generate_task(self, topic):
        try:
            self.update_result("⏳ 正在思考爆款标题，请稍候...")
            result = self.chain.invoke({"topic": topic})
            self.update_result(f"🎉 生成成功！\n\n{result}")
        except Exception as e:
            self.show_error(f"生成失败：{str(e)}")
        finally:
            self.generate_button.configure(state="normal")

    def generate_titles(self):
        topic = self.topic_entry.get().strip()
        if not topic:
            self.show_error("主题不能为空，请先输入主题！")
            return
            
        self.generate_button.configure(state="disabled")
        
        # 使用多线程防止 GUI 卡死
        threading.Thread(target=self.generate_task, args=(topic,), daemon=True).start()

if __name__ == "__main__":
    app = App()
    app.mainloop()
