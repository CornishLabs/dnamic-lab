

import pypicosdk as psdk

scope = psdk.ps5000a()

scope.open_unit()

# Print scope serial (Optional)
print(scope.get_unit_serial())

# Do something here

scope.close_unit()
