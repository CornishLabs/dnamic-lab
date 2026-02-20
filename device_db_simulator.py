from device_db import device_db
from dax.sim import enable_dax_sim as maybe_enable_dax_sim

device_db = maybe_enable_dax_sim(device_db, enable=True)