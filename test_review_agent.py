from review_agent import ClothingReviewAgent

def test_clothing_agent():
    print("初始化服饰评价 Agent...")
    try:
        agent = ClothingReviewAgent()
    except Exception as e:
        print(f"初始化失败: {e}")
        return

    # 伪造一个女装商品数据
    product_name = "法式复古收腰显瘦碎花连衣裙女夏2025新款"
    selling_points = ["V领显脸小", "雪纺面料透气不闷", "高腰线很显高", "不起球不褪色"]
    
    print("-" * 50)
    print(f"测试商品: {product_name}")
    print(f"主打卖点: {selling_points}")
    print("-" * 50)

    try:
        # 调用大模型生成 5 条评价
        result = agent.generate_reviews(product_name, selling_points, count=5)
        
        print("\n✨ 生成结果展示 ✨\n")
        for i, review in enumerate(result.reviews, 1):
            print(f"【评价 {i}】")
            print(f" 内容: {review.review_text}")
            print(f"📸 配图建议: {review.photo_suggest}")
            print("-" * 50)
            
    except Exception as e:
        print(f"\n生成过程中发生错误: {e}")

if __name__ == "__main__":
    test_clothing_agent()
