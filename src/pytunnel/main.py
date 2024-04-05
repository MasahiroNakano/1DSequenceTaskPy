
from flip_tunnel_functions import make_flip_tunnel
from misc import default_main, save_yaml

import atexit
import signal


options = default_main()
save_yaml(options)
no_input_speed = not ('speed' in options['inputs'])
flip_tunnel = make_flip_tunnel(
    options,
    test_mode=no_input_speed
)

# Define a function to clear tasks when the script exits


def cleanup():
    if flip_tunnel:
        print('cleanup')
        flip_tunnel.close()
        # try:
        #     flip_tunnel.close()
        # except:
        #     print('failed to close flip_tunnel')

# Define a function to handle Ctrl+C


def handle_ctrl_c(signum, frame):
    cleanup()
    exit(1)


# Register the cleanup function to be called on script exit
atexit.register(cleanup)
# Register the Ctrl+C handler
signal.signal(signal.SIGINT, handle_ctrl_c)

flip_tunnel.run()
