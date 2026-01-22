import json
# import logging

manage_frame_check=False

app_version='3.5'
host = '127.0.0.1'
port = 7497
clientId = 99

#  this is important in atr check  and adding in tp price
add002 = 0.02

pullBackNo=3
atrValue=50
atrPeriod=20

ibContract = "Stock"
whatToShow="TRADES"
durationStr="1 D"

# ibContract = "Forex"
# whatToShow="MIDPOINT"
# durationStr="1 D"

entryLastDataNo=[1,2,3]
# tradingTime='09:30:00'
outsideRthTradingtime='09:30:00'
#  in manage position it is working
LB1_PBe1_time='09:30:00'

pull_back_PBe1_time = '09:31:01'
first_bar_fb_time = '09:31:01'
pull_back_PBe2_time= '09:32:02'
tradingTime='09:30:00'
# tradingTime = json.load(open('datetime.json'))['time']
# logging.info(f"trading start timeis {tradingTime}")
# outsideRthTradingtime = tradingTime

# tradingEnd = '19:00:00'
tradingEnd = '19:00:00'


orderStatusData = {}
orderFilledPrice = {}
connectionObj = {}
defaultValue={}
historicalData={}
currentPnl = 0
pbe1_saved = {}
order_replay_pending = {}  # Map (symbol, timeFrame, barType, buySell, timestamp) to replay state for pending orders
option_trade_params = {}  # Map trade_key to option trading parameters (contract, expire, order types)

timeFrame = ['1 min', '2 mins', '3 mins', '5 mins', '10 mins', '15 mins', '30 mins','1 hour']
timeDictInMinute ={'1 min':1, '2 mins':2, '3 mins':3, '5 mins':5, '10 mins':10, '15 mins':15, '20 mins':20, '30 mins':30, '1 hour':60,
'2 hours':120, '3 hours':180, '4 hours':240}

timeDict={'1 min':60, '2 mins':120, '3 mins':180, '5 mins':300, '10 mins':600, '15 mins':900, '20 mins':1200, '30 mins':1600, '1 hour':3600,
'2 hours':7200, '3 hours':10800, '4 hours':14400}
takeProfit=['1:1','1.5:1','2:1','2.5:1','3:1']
stopLoss=['EntryBar','Custom','BarByBar' , 'HOD' , 'LOD','10% ATR','20% ATR','25% ATR','33% ATR','50% ATR']
atrStopLossMap = {
'10% ATR':0.10,
'20% ATR':0.20,
'25% ATR':0.25,
'33% ATR':0.33,
'50% ATR':0.50
}
timeInForce=['DAY','OTH','GTC']

# recent rbb
# recet bar [rb, recent bar BY Bar]
# pullbacj divide in two -------------  new pbe1 (close second trade)   new e2   old pbe1e2
manualOrderTypes = ['Custom','Limit Order']
entryTradeType=manualOrderTypes + ['Conditional Order','FB','RB','RBB','PBe1','PBe2','LB','LB2','LB3']
buySell=['BUY','SELL']
prePostBool=[False,True]
breakEven =[False,True]



title = 'New Trade'
fontName = 'Times New Roman'
fontSize = 15
fontName2 = 'Times New Roman'
fontSize2 = 12

roundVal=2

# Option trading configuration
optionOrderTypes = ['Market', 'Bid+', 'Ask-']
option_trade_params = {}
optionRiskAmount = []
pending_option_orders = {}  # Store pending orders that couldn't be placed because condition was already met



