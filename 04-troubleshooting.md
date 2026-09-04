# **🤔 Common Troubleshooting / Known Bugs**

**Most Common Bug; Meta Quest Link Disconnecting Mid-Session:**

- WARNING: This fix will require you to roll back your graphics driver. Check if anyone using this computer needs the graphics driver unfixed or if another application you are using requires a modern version  
- As of 8/3/2026, there has been an ongoing bug with Meta Quest Link disconnecting mid-session due to NVIDIA GPU drivers  
  - Typically, this looks like GHOST freezing and the screen becoming pixelated and black on the edge of your VR view  
  - This crash happens anytime between 0-20 minutes of teleoperation  
- Go into Task Manager and check the version of your current GPU. If the version is after 591.XX, you will need to roll back your graphics driver to 591.XX (any driver released during January 2026\)  
  - I have rolled it back to 591.74 and have had success  
- Run a clean install of the appropriate graphics driver with version 591.XX

**Unity Play Mode Has a Black Screen and Meta Link Shows a Rotating Hourglass:**

- Pressing the pause button in Unity Editor will make everything run normally

**Running ROS Server With Specific Spot (say spot2) and Pointcloud is Not Showing in VR**

- Press the button to cycle through pointclouds

**Stow Arm Button is Greyed Out**

- You can stow the spot arm by pressing the back button (arm control button) and pressing the joystick

**Unity is Not Connecting to the Correct ROS IP Address (Errors/Warning in Unity Console About Some IP Address)**

- Many places on the Unity side have hard coded their IP addresses. I have included a script to change these IP addresses at compile time. In the script, change the IP address to be the appropriate ROS server address  
- If you still have an issue, you may need to have a component added (such as TFConnector) to a GameObject. I recommend asking an AI tool (e.g. Claude Code) to parse through the Unity files and find where you might need to change an IP address/add a new component to a GameObject

