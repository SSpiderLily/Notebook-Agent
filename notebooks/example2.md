---
title: 机器学习入门
author: NoteAgent
tags: [机器学习, AI, 数据科学]
created: 2026-03-02
---

# 机器学习入门

机器学习是人工智能的核心技术之一。

## 主要类型

### 监督学习

使用标记的数据进行训练。

```python
from sklearn.linear_model import LinearRegression

model = LinearRegression()
model.fit(X_train, y_train)
```

### 无监督学习

从未标记的数据中发现模式。

### 强化学习

通过奖励机制学习最优策略。

## 常用算法

- 线性回归 #算法
- 决策树 #算法
- 支持向量机 #算法
- 神经网络 #算法 #深度学习

## 应用场景

- 图像识别
- 自然语言处理
- 推荐系统
- 预测分析

## 学习资源

- [吴恩达机器学习课程](https://www.coursera.org/learn/machine-learning)
- [Scikit-learn文档](https://scikit-learn.org)
