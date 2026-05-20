# N型B1选股策略

基于N型结构筛选出符合B1条件的股票

## 筛选条件

0. 这个策略只是筛选出股票, 不做买卖操作,但要求每次都绘制出 charts 相关的图表(每次执行前务必先清空 ./charts 目录)
1. 60日内至少出现两次B1信号,而且两次B1信号之间间隔时间超过30天;如果白线刚刚金叉黄线上来,则不要求前面出现过B1信号,只要金叉之后出现B1信号即可
2. 要求每次B1 信号都要比之前的B1信号价格高, 这样才符合N型低点抬高的结构
3. 股票流通市值50亿以上
4. 剔除掉股票前期没有缩量上涨(剔除前期有缩量上涨情况的股票),前期如果有连续涨停并且缩量的情况直接剔除;前期连续上涨后有放量下跌的也要剔除(特别是下跌量比前期的都要多的那种);阶梯放量出货的股票剔除;剔除前期高点长上影线;剔除前期有S1或者大风车的股票;放量上涨才行 + B1 缩量最佳
5. 统计选股后T+5 涨幅超过10%的概率
6. 能支持指定日期区间进行股票筛选

## 假案例识别

识别到以下案例的股票, 要剔除掉

1. 快速拉升出货,然后平量出货,这种要剔除: 601198 2024-04-26 到2024-05-06 ;
2. 拉升不均匀不是价升量涨类型,量一会多一会少,涨幅也不归路,剔除: 601222 2024-04-24 到2024-05-13 ;
3. 前面一个B1 之后股价又跌破黄线,黄白线死叉又金叉的,再拉上来的说明主力控盘实力不足,剔除: 601231 2024-03-15 到2024-05-30 ;
4. 前期连续跳空拉升,然后缩量横盘出货,最后一天还放量出货,这种要剔除: 603855 2024-03-28 到2024-04-29 ;
5. 这种就是S1顶部放量,这种要剔除: 000060 2024-04-08 ;
6. 不要选拉升阶段不连续的,量价忽大忽小,涨幅也不多的,这种要剔除: 601222 2024-04-26 到2024-05-14 ;
7. 这种也是阶梯量出货,这种要剔除: 603568 2024--4-17 到 2024-04-25 和 2024-04-26 到2024-05-10 ;
8. B1前5日有跌停的,这种要剔除

## B1选股公式

- 筛选出股票MACD处于多头空间
- 每个交易日运行一次,选出缩量最好的1支股票,并且流动市值较大的股票

```js
{参数默认值，可根据需要修改}
N:=20; M:=50;
M1:=14; M2:=28; M3:=57; M4:=114;  {大哥黄线均线周期}
N1:=3; N2:=21;                   {单针短期/长期周期}

{趋势线}
趋势白线:=EMA(EMA(C,10),10);
大哥黄线:=(MA(CLOSE,M1)+MA(CLOSE,M2)+MA(CLOSE,M3)+MA(CLOSE,M4))/4;

{单针指标}
SHORT:=100*(C-LLV(L,N1))/(HHV(C,N1)-LLV(L,N1));
LONG:=100*(C-LLV(L,N2))/(HHV(C,N2)-LLV(L,N2));
BBI:=(MA(CLOSE,3)+MA(CLOSE,6)+MA(CLOSE,12)+MA(CLOSE,24))/4;

{振幅与异动判定}
振幅区间:=IF(CODELIKE('68') OR CODELIKE('30') OR CODELIKE('4') OR CODELIKE('8') OR CODELIKE('9') OR EXIST(C/REF(C,1)>1.15,200), 8, 5);
放宽系数:=IF(CODELIKE('68') OR CODELIKE('30') OR CODELIKE('4') OR CODELIKE('8') OR CODELIKE('9') OR EXIST(C/REF(C,1)>1.15,200),0.9,1);
当日振幅:=(HIGH - LOW) / LOW * 100;
当日涨跌幅:=ABS(CLOSE - REF(CLOSE, 1)) / REF(CLOSE, 1) * 100 * 放宽系数;
上涨十字星:=C>REF(C,1) AND (ABS(C-O)/O*100 * 放宽系数)<1.8;
单针下20:=(SHORT<=20 AND LONG>=75) OR ((LONG-SHORT)>=70);
聚宝盆:=COUNT(LONG>=75,8)>=6 AND COUNT(SHORT<=70,7)>=4 AND COUNT(SHORT<=50,8)>=1;
双叉戟:=EVERY(LONG>=75,8) AND COUNT(SHORT<=50,6)>=2 AND COUNT(SHORT<=20,7)>=1;
红肥绿瘦:=COUNT(C>=O,15)>7 OR COUNT(C>REF(C,1),11)>5;
近期振幅:=(HHV(HIGH,N) - LLV(LOW,N)) / LLV(LOW,N) * 100;
近期异动:=近期振幅>=15 OR (HHV(H,12)-LLV(L,14))/LLV(L,14)*100>=11;
远期振幅:=(HHV(HIGH,M) - LLV(LOW,M)) / LLV(LOW,M) * 100;
远期异动:=远期振幅>=30;
超级异动:=近期振幅>=60;
洗盘异动:=(COUNT(单针下20,10)>=2) OR (聚宝盆) OR 双叉戟;

{成交量辅助}
VDAY:=HHVBARS(VOL,40);
不是大绿棒:=REF(C,VDAY)>=REF(C,VDAY+1) OR REF(C,VDAY)>=REF(O,VDAY);
大绿棒:=NOT(不是大绿棒);
大绿棒离得远:=VDAY>=15 AND 大绿棒;
缩量:=(VOL < HHV(VOL,20)*0.416) OR (VOL < HHV(VOL,50)/3);
回踩缩量:=(VOL < HHV(VOL,20)*0.45) OR (VOL < HHV(VOL,50)/3);
适当缩量:=(VOL < HHV(VOL,20)*0.618) OR (VOL < HHV(VOL,50)/3);
超缩量:=(VOL < HHV(VOL,30)/4) OR (VOL < HHV(VOL,50)/6);

{KDJ与RSI}
J:=KDJ.J;
K:=KDJ.K;
LC:=REF(CLOSE,1);
TEMP1:=MAX(CLOSE-LC,0);
TEMP2:=ABS(CLOSE-LC);
RSI:=SMA(TEMP1,3,1)/SMA(TEMP2,3,1)*100;

{趋势状态}
做上涨趋势:=趋势白线>=大哥黄线*0.999 AND (C>=大哥黄线 OR (C>大哥黄线*0.975 AND C>O));
强趋势股:=EVERY(大哥黄线>=REF(大哥黄线,1)*0.999,13) AND 趋势白线>=REF(趋势白线,1) AND EVERY(趋势白线>大哥黄线,20) AND EVERY(趋势白线>=REF(趋势白线,1),11) AND 红肥绿瘦;
超牛股:=(EVERY(BBI>=REF(BBI,1)*0.999,20) OR COUNT(BBI>=REF(BBI,1),25)>=23) AND (近期振幅>=30 OR 远期振幅>80) AND BARSLAST(CROSS(C,大哥黄线))>12;

{回踩距离}
距离白线:=ABS(C-趋势白线)/C*100;
L距离白线:=(ABS(L-趋势白线)/趋势白线)*100;
距离BBI:=ABS(C-BBI)/C*100;
L距离BBI:=(ABS(L-BBI)/BBI)*100;
回踩白线:=(C>=趋势白线 AND 距离白线<=2) OR (C<趋势白线 AND 距离白线<0.8) OR (C>=BBI AND 距离BBI<2.5 AND L距离BBI<1 AND 距离白线<=3 AND 当日涨跌幅<1 AND C>REF(C,1));
白线支撑:=C>=趋势白线 AND 距离白线<1.5;
强势回踩不破:=(L距离白线<1 OR L距离BBI<0.5) AND (C>趋势白线) AND (距离白线<=3.5);
距离黄线:=(ABS(C-大哥黄线)/大哥黄线)*100;
回踩黄线:=(C>=大哥黄线 AND (距离黄线<=1.5 OR (距离黄线<=2 AND 当日涨跌幅<1))) OR (C<大哥黄线 AND 距离黄线<=0.8);

{买入提示B条件}
超卖缩量拐头B:=做上涨趋势 AND (RSI-15)>=REF(RSI,1) AND (REF(RSI,1)<20 OR REF(J,1)<14) AND 当日振幅<(振幅区间+0.5) AND (当日涨跌幅<2.3 OR (上涨十字星 AND 当日涨跌幅<4)) AND (不是大绿棒 OR 大绿棒离得远) AND (近期异动 OR 远期异动 OR 洗盘异动) AND C>=大哥黄线;
超卖缩量B:=做上涨趋势 AND (J<14 OR RSI<23) AND (RSI+J<55 OR J=LLV(J,20)) AND 当日振幅<振幅区间 AND (当日涨跌幅<2.5 OR 上涨十字星) AND (不是大绿棒 OR 大绿棒离得远) AND (缩量 OR (适当缩量 AND 当日涨跌幅<1)) AND (近期异动 OR 远期异动 OR 洗盘异动);
原始B1:=趋势白线>大哥黄线 AND C>=大哥黄线*0.99 AND 大哥黄线>=REF(大哥黄线,1) AND (J<13 OR RSI<21) AND (RSI+J)<LLV(RSI+J,15)*1.5 AND 适当缩量 AND (不是大绿棒 OR 大绿棒离得远) AND (ABS(C-O)*100/O<1.5 OR (超缩量 OR (适当缩量 AND V<LLV(V,20)*1.1 AND J=LLV(J,20))) OR (适当缩量 AND (距离白线<1.8 OR 距离BBI<1.5 OR 距离黄线<2.8))) AND (近期异动 OR 远期异动 OR 洗盘异动);
超卖超缩量B:=做上涨趋势 AND (J<14 OR RSI<23) AND RSI+J<60 AND 远期振幅>=45 AND (当日振幅<振幅区间 OR (超级异动 AND 当日振幅<振幅区间+3.2 AND C>O AND C>趋势白线)) AND ((C<O AND V<REF(V,1) AND C>=大哥黄线) OR (C>=O)) AND (当日涨跌幅<2 OR 上涨十字星) AND (不是大绿棒 OR 大绿棒离得远) AND 超缩量 AND (近期异动 OR 远期异动 OR 洗盘异动);
回踩白线B:=强趋势股 AND (J<30 OR RSI<40 OR 洗盘异动) AND RSI+J<70 AND (当日振幅<振幅区间+0.5 OR 距离白线<1 OR 距离BBI<1) AND 回踩白线 AND (当日涨跌幅<2 OR (当日涨跌幅<5 AND 白线支撑)) AND (不是大绿棒 OR 大绿棒离得远) AND 回踩缩量 AND (近期异动 OR 远期异动 OR 洗盘异动) AND L<=REF(C,1);
回踩超级B:=超牛股 AND (J<35 OR RSI<45 OR 洗盘异动) AND RSI+J<80 AND (RSI+J)=LLV(RSI+J,25) AND 当日振幅<振幅区间+1 AND (当日涨跌幅<2.5 OR 距离白线<2) AND 强势回踩不破 AND (不是大绿棒 OR 大绿棒离得远) AND (近期异动 OR 远期异动 OR 洗盘异动) AND 适当缩量;
回踩黄线B:=趋势白线>=大哥黄线 AND C>=大哥黄线*0.975 AND (J<13 OR RSI<18) AND 回踩黄线 AND (不是大绿棒 OR 大绿棒离得远) AND (缩量 OR (适当缩量 AND (J=LLV(J,20) OR RSI=LLV(RSI,14)))) AND 大哥黄线>=REF(大哥黄线,1)*0.997 AND MA(C,60)>=REF(MA(C,60),1) AND 近期振幅>=11.9 AND 远期振幅>=19.5;
存在B:=超卖缩量拐头B OR 超卖缩量B OR 原始B1 OR 超卖超缩量B OR 回踩白线B OR 回踩超级B OR 回踩黄线B;

{选股输出}
XG:存在B;
```
