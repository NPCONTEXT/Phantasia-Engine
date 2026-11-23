"""商店采购：按商品卡分组，核对本商品售罄与金币扣款；刷新价不明则不刷新。"""
from __future__ import annotations
import sys,time,re,unicodedata,cv2,numpy as np
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.er_runtime import Controller,RunStopped,button
from scripts import er_role


def normal(text):
    # OCR会漏掉/替换标题中的间隔点；保留文字、数字和等级加号。
    return ''.join(c for c in unicodedata.normalize('NFKC',text) if c.isalnum() or c=='+')


def group_items(items):
    badges=[i for i in items if i.get('x',0)>2100 and 100<i.get('y',0)<1310
            and i.get('text','').strip() in ('普通','晋通','核心','增幅','专属')]
    titles=[]
    for badge in badges:
        near=[i for i in items if 600<i.get('x',0)<1900 and i.get('w',9999)<800
              and i.get('h',0)>40 and abs(i['y']-badge['y'])<85]
        if near:titles.append(min(near,key=lambda i:abs(i['y']-badge['y'])))
    titles=sorted({normal(i['text']):i for i in titles}.values(),key=lambda i:i['y'])
    goods=[]
    for n,title in enumerate(titles):
        end=titles[n+1]['y']-10 if n+1<len(titles) else 1340
        row=[i for i in items if i.get('x',0)>600 and title['y']-15<=i.get('y',0)<end]
        text=' '.join(i['text'] for i in row)
        prices=[]
        for i in row:
            if i.get('x',0)>2100 and i['y']>title['y']+55:
                m=re.search(r'(\d{2,3})\s*$',i['text'])
                if m:prices.append(int(m[1]))
        goods.append(dict(title=title,text=text,price=prices[0] if prices else None,sold='已售罄' in text))
    return goods


def good_score(title,desc):
    # 已售罄谁都不买；其余按角色档案的口味（elysium_roles/<角色>.py 的 shop_score，10-20 起）
    if '已售罄' in title+' '+desc:
        return -100
    return er_role.cur().shop_score(title,desc)


def balance(items):
    vals=[i for i in items if i.get('x',0)>2000 and i.get('y',999)<120]
    for item in vals:
        m=re.search(r'(\d{2,5})\s*$',item['text'])
        if m:return int(m[1])
    return None


def refresh_price(items):
    # 价格在标签右侧；不把币图标或别处商品价格当刷新费。
    label=button(items,'本次刷新消耗')
    if not label:return None
    right=label['x']+label['w']
    candidates=[i for i in items if right<i.get('x',0)<right+240 and abs(i.get('y',0)-label['y'])<50
                and i.get('conf',0)>=.5 and re.fullmatch(r'\d{1,3}',i.get('text','').strip())]
    return int(candidates[-1]['text']) if candidates else None


def receipt(items,title,before,price):
    matching=next((g for g in group_items(items) if normal(g['title']['text'])==normal(title)),None)
    after=balance(items)
    paid=before is not None and after is not None and price is not None and before-after==price
    return bool(matching and matching['sold'] and paid)


def scroll(c,frame):
    if frame.state!='shop':c.stop('非商店页面禁止商品滚动')
    c._fresh(frame)
    c._input(lambda:c.adb.swipe(1400,1120,1400,650,300),'scroll_shop',False)


SOLD_GAP=62      # 11-03 实机：「已售罄」那一行的上沿在商品说明最后一行上沿下面约 62 像素（说明 830 → 售罄 892；识字坐标是左上角）
LINE_H=45        # 一行字高，那一行整行露出来才认得到
BAR_TOP=1340     # 商品列表下沿，底栏从这里起（group_items、selected_border 用的同一个数）


def hidden_bottom(items,good):
    """11-11：要买的是列表里最后一张卡，最底下那行字再往下一行（「已售罄」那一行）会落到底栏下面 → True，买前先往上滚。
    11-10 第 16 层：「亵渎不归之「爪」+1」说明 4 行、最后一行 y=1273，买成了（金币 2697→2532），
    但「已售罄」被底栏挡住认不到，receipt 核实不了、误停。"""
    y0=good['title']['y']
    if any(g['title']['y']>y0+20 for g in group_items(items)):return False   # 下面还有别的商品卡：这张露全了
    low=y0      # 本卡最后一行：从标题往下、行距不超过 90 的连着算（下一张卡认不出角标时，它的字不算进来）
    for y in sorted(i['y'] for i in items if i.get('x',0)>600 and y0-15<=i.get('y',0)<BAR_TOP):
        if y>low+90:break
        low=max(low,y)
    return low+SOLD_GAP+LINE_H>BAR_TOP


def paid_but_hidden(items,title,before,price):
    """11-11：买完金币正好扣了 price、本商品卡还在但没认到「已售罄」，而且它是最后一张、售罄那一行会落到底栏下（11-10 爪）。"""
    after=balance(items)
    if before is None or after is None or price is None or before-after!=price:return False
    good=next((g for g in group_items(items) if normal(g['title']['text'])==normal(title)),None)
    return bool(good and not good['sold'] and hidden_bottom(items,good))


def selected_border(png,title):
    im=cv2.resize(cv2.imdecode(np.frombuffer(png,np.uint8),1),(2400,1504))
    gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
    y=round(title['y'])
    if y+55>=1340:return 0
    # 选中卡片左边框变亮；用卡片内部高度避开相邻卡片的上下边框。
    return max(float(gray[y+55:min(y+145,1340),x:x+2].mean()) for x in range(410,450))


def stable_shop(c,frame):
    deadline=time.monotonic()+6
    previous=None;stable_since=None
    while time.monotonic()<deadline:
        if frame.state!='shop':c.stop('商品稳定检查时离开商店页')
        signature=[(normal(g['title']['text']),g['title']['y']) for g in group_items(frame.items)]
        stable=previous and signature and len(previous)==len(signature) and all(
            a[0]==b[0] and abs(a[1]-b[1])<6 for a,b in zip(previous,signature))
        stable_since=(stable_since or time.monotonic()) if stable else None
        if stable_since is not None and time.monotonic()-stable_since>=.4:return frame
        previous=signature
        frame=c.observe()
    c.stop('商品列表6秒仍未稳定，禁止按滚动中的位置购买')


def run_shop(c,frame,max_operations=10):
    bought=set();scrolls=0;refreshes=0;lifts=0
    for rnd in range(max_operations):
        frame=stable_shop(c,frame)
        if frame.state!='shop':c.stop('采购时离开商店页')
        coins=balance(frame.items)
        goods=group_items(frame.items)
        choices=[g for g in goods if not g['sold'] and normal(g['title']['text']) not in bought and g['price'] is not None
                 and coins is not None and coins>=g['price'] and good_score(g['title']['text'],g['text'])>=er_role.cur().SHOP_MIN_SCORE]
        if choices:
            best=max(choices,key=lambda g:good_score(g['title']['text'],g['text']))
            if lifts<2 and hidden_bottom(frame.items,best):
                # 11-11：最后一张卡底下没露全（「已售罄」会落到底栏下认不到）→ 先往上滚、露全了再买；滚 2 次还这样就照旧买
                c.log('shop_lift',title=best['title']['text'],title_y=best['title']['y'])
                scroll(c,frame);lifts+=1;frame=c.observe();continue
            title=best['title']['text'];price=best['price']
            c.tap(1300,best['title']['y']+25,frame)
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                selected=c.observe()
                target=next((g for g in group_items(selected.items) if normal(g['title']['text'])==normal(title)),None)
                if selected.state=='shop' and target and selected_border(selected.png,target['title'])>=115:break
            else:c.stop('目标商品没有出现选中高亮，禁止点购买')
            if selected.state!='shop':c.stop('选择商品后商店页消失')
            selected_goods=group_items(selected.items)
            selected_item=next((g for g in selected_goods if normal(g['title']['text'])==normal(title)),None)
            if not selected_item or selected_item['sold'] or selected_item['price']!=price or balance(selected.items)!=coins:
                c.stop('选中后商品/价格/余额发生变化，保留商店现场')
            c.save('shop_selected',selected)
            c.log('purchase_attempt',title=title,price=price,before=coins,selection_border=round(selected_border(selected.png,selected_item['title']),1))
            buy=button(selected.items,'购买刻印',roi=(1900,1300,2400,1504))
            if not buy:c.stop('未识别到商品购买按钮')
            c.tap_item(buy,selected)
            deadline=time.monotonic()+6;lifted=False
            while time.monotonic()<deadline:
                frame=c.observe()
                if not lifted and frame.state=='shop' and paid_but_hidden(frame.items,title,coins,price):
                    # 11-11：金币正好扣了、本商品在最后一张、「已售罄」那一行被底栏挡住（买完卡片多出一行，列表到底时露不出来）
                    # → 只往上滚一次再认；认到售罄才算买成，认不到照旧停
                    c.log('shop_receipt_lift',title=title,after=balance(frame.items))
                    scroll(c,frame);lifted=True;deadline=max(deadline,time.monotonic()+3)
                    continue
                if receipt(frame.items,title,coins,price):
                    bought.add(normal(title))
                    c.log('purchase_verified',title=title,price=price,before=coins,after=balance(frame.items))
                    getattr(c,'seals',[]).append(normal(title))
                    c.save('shop_last_receipt',frame)
                    break
            else:c.stop(f'购买{title}后未同时核实金币扣款和本商品售罄，停止重复提交')
            continue
        if scrolls<2:
            scroll(c,frame);scrolls+=1;frame=c.observe();continue
        cost=refresh_price(frame.items)
        if cost is None or cost>229 or coins is None or cost>coins or refreshes>=2:
            c.log('shop_done',bought=len(bought),refreshes=refreshes,refresh_cost=cost,
                  reason='无合适商品；刷新成本未可靠识别/超过预算/次数用尽')
            return frame
        c.tap(470,1410,frame)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            confirm=c.observe()
            if '是否刷新商店' in ' '.join(i['text'] for i in confirm.items):break
        else:c.stop('刷新点击后没有商店刷新确认框')
        ok=button(confirm.items,'确认',exact=True)
        if not ok:c.stop('刷新确认按钮缺失')
        c.tap_item(ok,confirm)
        refreshes+=1;scrolls=0;lifts=0;bought.clear()
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            frame=c.observe()
            if frame.state=='shop' and balance(frame.items)==coins-cost:break
        else:c.stop('刷新后未核实商店页与刷新扣款')
        c.log('shop_refresh_verified',cost=cost,before=coins,after=balance(frame.items))
    c.log('shop_done',bought=len(bought),refreshes=refreshes,reason='操作预算用尽')
    return frame


def main():
    c=Controller()
    try:run_shop(c,c.observe(),int(sys.argv[1]) if len(sys.argv)>1 else 10)
    except RunStopped as exc:print('采购停止:',exc,flush=True);return 2
    finally:c.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
