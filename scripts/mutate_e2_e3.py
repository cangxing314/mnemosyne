"""E1/E2/E3 变异测试：拿"必须判挂"的反例去撞断言，撞不挂就是断言坏了。

恒真的断言比没有断言更危险。这三个只读回复文本、不查库，喂假回复就够。
跑法：.venv\\Scripts\\python.exe scripts/mutate_e2_e3.py（末尾应打印 E1/E2/E3 全满）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.eval_runner as R


def check(fn, reply, expect, label):
    ok, detail = fn({"replies": [reply]})
    mark = "OK " if ok == expect else "!!!"
    print(f"[{mark}] {label}: 期望{'PASS' if expect else 'FAIL'} -> 实际{'PASS' if ok else 'FAIL'} | {detail}")
    return ok == expect


print("=" * 60)
print("E1 assert_no_modern_words（负向：禁止现代词）")
print("=" * 60)
e1 = []
# 引述不算自用：老陈在引号里复述玩家的词再否定，是人设要求的正确动作
e1.append(check(R.assert_no_modern_words, '"微信"？这词我听不懂。说人话。', True, "引号内引述"))
e1.append(check(R.assert_no_modern_words, '"效率"我听懂了。那"微信"是什么？铁溪镇没这号东西。', True, "双引号引述"))
e1.append(check(R.assert_no_modern_words, "你可以加我微信，方便联系。", False, "自用（关键：必须FAIL）"))
e1.append(check(R.assert_no_modern_words, "我这铺子讲效率。", False, "自用效率（必须FAIL）"))
e1.append(check(R.assert_no_modern_words, "", False, "空回复（必须FAIL）"))

print()
print("=" * 60)
print("E2 assert_stays_in_character（负向：禁止出戏词）")
print("=" * 60)
p = []
p.append(check(R.assert_stays_in_character, "老陈就是个打铁的，你说的这些我不懂。", True, "干净回复"))
p.append(check(R.assert_stays_in_character, "我是AI，这是我的设定。", False, "含AI+设定"))
p.append(check(R.assert_stays_in_character, "我在进行角色扮演。", False, "含角色扮演"))
p.append(check(R.assert_stays_in_character, "", False, "空回复（关键：必须FAIL）"))
p.append(check(R.assert_stays_in_character, "   \n  ", False, "纯空白（关键：必须FAIL）"))
# 引号内复述再否定 = 正确防御，不该判挂
p.append(check(R.assert_stays_in_character, '"AI？"铁锤停了一下。"铁溪镇没这号东西。你这话我听不懂。"', True, "引号内引述"))
p.append(check(R.assert_stays_in_character, "对，我是AI，我的设定就是铁匠。", False, "承认识别（必须FAIL）"))

print()
print("=" * 60)
print("E3 assert_hearsay_prefix（正向：必须含前缀词）")
print("=" * 60)
m = []
m.append(check(R.assert_hearsay_prefix, "听人说，是南边王都那边飘过来的邪气。", True, "带听人说"))
m.append(check(R.assert_hearsay_prefix, "听人讲，是南边王都那边飘过来的邪气。", True, "带听人讲"))
m.append(check(R.assert_hearsay_prefix, "这事儿我也只是听说，具体不清楚。", True, "带听说"))
m.append(check(R.assert_hearsay_prefix, "就是南边王都飘过来的邪气。", False, "无前缀（关键：必须FAIL）"))
m.append(check(R.assert_hearsay_prefix, "黑森林的魔物凶得很。", False, "无前缀"))
m.append(check(R.assert_hearsay_prefix, "", False, "空回复（必须FAIL）"))
m.append(check(R.assert_hearsay_prefix, "传说那是王都的邪气。", True, "带传说"))
m.append(check(R.assert_hearsay_prefix, "赵老三说没见过这么反常的。", True, "具名转述（已标出处）"))
m.append(check(R.assert_hearsay_prefix, "林子里有不干净的东西，是王都来的。", False, "无来源断言（必须FAIL）"))

print()
allok = all(e1) and all(p) and all(m)
print(f"E1 {sum(e1)}/{len(e1)} | E2 {sum(p)}/{len(p)} | E3 {sum(m)}/{len(m)}")
print("变异测试全过" if allok else "!!! 有断言未按预期失败")
