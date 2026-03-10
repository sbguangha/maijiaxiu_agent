"""
V2.0 Agent 测试脚本
测试两种场景：直接输入商品标题 / 输入淘宝链接
"""
from agent_graph import run_agent

def test_direct_title():
    """场景1：用户直接给商品标题和卖点"""
    print("=" * 60)
    print("🧪 测试场景1：直接给商品标题")
    print("=" * 60)
    
    user_input = "帮我给这个商品生成5条买家评价：2025新款男士纯棉圆领短袖T恤，卖点是纯棉不起球、不缩水、百搭显瘦"
    
    print(f"📝 用户输入: {user_input}\n")
    result = run_agent(user_input)
    print(f"🤖 Agent 输出:\n{result}")


def test_with_url():
    """场景2：用户给一个商品链接"""
    print("\n" + "=" * 60)
    print("🧪 测试场景2：给商品链接（可能被反爬）")
    print("=" * 60)
    
    user_input = "帮我生成这个商品的买家秀评价：https://detail.tmall.com/item.htm?id=123456"
    
    print(f"📝 用户输入: {user_input}\n")
    result = run_agent(user_input)
    print(f"🤖 Agent 输出:\n{result}")


if __name__ == "__main__":
    print("🚀 V2.0 Agent 测试开始...\n")
    
    # 先测试场景1（直接标题，100%能成功）
    test_direct_title()
    
    # 再测试场景2（URL，预计会被反爬，Agent 应该能优雅降级）
    # test_with_url()  # 取消注释可测试
    
    print("\n✅ 测试完成！")
