from tkinter import *
from ib_insync import *
import asyncio
import datetime
import nest_asyncio
import Config
import tkinter.messagebox
from tkinter import ttk
import numpy as np
from os import path

import logging
logging.basicConfig(filename='IB.log', filemode='a',
                    format='%(asctime)s  - %(name)s - %(funcName)s - %(lineno)d - %(levelname)s - %(message)s', level=logging.INFO)
# Reduce noise: ib_insync and werkzeug log at DEBUG/INFO; keep only WARNING+ for them
logging.getLogger('ib_insync').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
