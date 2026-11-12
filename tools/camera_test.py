import cv2

print("Starting camera test...")

camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)

print("Camera opened:", camera.isOpened())

if not camera.isOpened():
    print("❌ Camera open nahi hua")
    exit()

while True:

    ret, frame = camera.read()

    if not ret:
        print("❌ Frame nahi mila")
        break

    cv2.imshow("AI Sentinel Camera Test", frame)

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break

camera.release()
cv2.destroyAllWindows()

print("Camera test finished")