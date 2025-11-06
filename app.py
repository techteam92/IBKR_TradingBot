from header import *
from NewTradeFrame import *
from ManagePositionFrame import *
from IBConnection import connection
from Config import app_version,tradingTime
from StatusSaveInFile import *
class TkApp:

    #  dialog box will open from this
    def __init__(self):
        logging.info(f'front gui start.  {app_version} {tradingTime}')
        self.connection = connection()
        self.connection.connect()
        self.connection.reqPnl()
        self.frame = Tk()
        self.dialog()
        self.frontLayout()
        self.loop = asyncio.get_event_loop()

    # this will run our tkinter and Ib  event will not override.
    async def _tkLoop(self):
        while self.frame:
            self.frame.update()
            await asyncio.sleep(0.03)

    def run(self):
        try:
            logging.info("App Initializing")
            self.loop.run_until_complete(self._tkLoop())
        except Exception as e:
            print(str(e))

    def dialog(self):
        self.frame.title(Config.title)
        self.frame.protocol("WM_DELETE_WINDOW", self.close_window)
        self.frame.geometry(
            "%dx%d+%d+%d" % (1200, 620, (self.frame.winfo_screenwidth() / 2) - 400, (self.frame.winfo_screenheight() / 2) - 350))
        menubar = Menu(self.frame, borderwidth=1, bg="#20232A")
        menubar.add_command(label="Manage Position", command=self.openManagePosition)
        menubar.add_command(label="Setting", command=self.Setting)
        menubar.add_command(label="Exit", command=self.close_window)
        self.frame.config(menu=menubar)
        loadCache(self.connection)





    def frontLayout(self):
        s = ttk.Style(self.frame)
        s.theme_use('clam')
        s.configure('raised.TMenubutton', borderwidth=1)
        # ManagePositionFrame(self.frame,self.connection)
        NewTradeFrame(self.frame, self.connection)
        #  Ib Connection Check
        self.connectionCheck()

    def close_window(self):
        StatusSaveInFile()
        logging.info("Shutdown Gui")
        self.connection.cancelTickData(Config.ibContract)
        self.connection.connection_close()
        self.frame = None
        sys.exit()

    def openManagePosition(self):
        if(Config.manage_frame_check):
            tkinter.messagebox.showinfo('Connection', 'Manage Position Frame Already Opened')
        else:
            Config.manage_frame_check = True
            ManagePositionFrame(self.connection)

    def Setting(self):
        DefaultSetting(self.connection)

    def connectionCheck(self):
        loop = 1
        error = 1
        while loop == 1:
            conStatus = self.connection.ibStatusCheck()
            loop += 1
            if not conStatus:
                logging.info("IB Connection failed want to retry")
                retryvar = tkinter.messagebox.askretrycancel('Connection', 'IB Connection failed want to retry?')
                if not retryvar:
                    loop += 1
                    self.close_window()
                else:
                    error = 2
                    self.connection.connect()
                    loop = 1
            else:
                if error == 2:
                    logging.info("TWS Connected")
                    tkinter.messagebox.showinfo('Connection', 'TWS connected')
                loop = 2


app = TkApp()
app.run()
