from utility.train_utils import save_helper
import torch


class Worker():
    def __init__(self, model, optimizer, criterion,  # model params
                 epochs, epoch_offset, step_offset,
                 train_loader, val_loader, test_loader,
                 device, writer, log_root, log_folder, best_prec1=0,
                 save=True,
                 save_best=True,
                 debug_mode=False,
                 debug_batch=5):
        self.model = model
        self.epochs = epochs
        self.epoch_offset = epoch_offset
        self.step_offset = step_offset
        self.abs_epoch = -1
        self.abs_step = -1
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.test_loader = test_loader
        self.writer = writer
        self.device = device
        self.optimizer = optimizer
        self.criterion = criterion
        # self.eval_inteval_epoch = eval_inteval_epoch
        # self.save_inteval_epoch = save_inteval_epoch
        self.best_prec1 = best_prec1
        self.log_folder = log_folder
        self.log_root = log_root

        self.save = save
        self.save_best = save_best
        self.debug_mode = debug_mode
        self.debug_batch = debug_batch

    def train(self, if_log=True):
        # Main loop config: https://discuss.pytorch.org/t/interpreting-loss-value/17665/4
        self.model.train(True)  # the train mode

        # iterate the dataset
        num_images = 0
        running_loss = 0
        running_acc = 0

        for step, batch in enumerate(iter(self.train_loader)):
            if self.debug_mode:
                if step+1 > self.debug_batch:
                    break
            self.abs_step = step + self.step_offset
            self.step_offset = self.abs_step + 1

            rgb, depth, label = batch['rgb'].to(self.device), batch['depth'].to(self.device), batch['label'].to(
                self.device)
            batch_size = rgb.size(0)
            num_images += batch_size

            # calculate output and loss
            o = self.model(rgb, depth)

            # optimization
            loss = self.criterion(o, label)  # + self.model.parameters().values()
            accu = torch.sum(
                torch.argmax(o, dim=1) == label
            )
            running_loss += (loss * batch_size).item()
            running_acc += accu.item()

            # back-propagation
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            if if_log:
                print('running at step {}'.format(step))
                print('logging at step {}'.format(self.abs_step))
                self.writer.add_scalar('Train/Running_Loss(steps)', loss.item(), self.abs_step)
                self.writer.add_scalar('Train/Running_Accu(steps)', float(accu.item()) / batch_size, self.abs_step)
                # self.writer.add_scalar('Train/Sanity of wz',
                #                        float(torch.sum(torch.abs(self.model.attens[0][0].Wz[0].weight.flatten())).item()),
                #                        self.abs_step)

        return running_loss, running_acc, num_images

    def validate(self, loader, mode='Validate', if_log=True):
        # validate:
        print(mode + ' at epoch{}'.format(self.abs_epoch))
        self.model.eval()  # switch to eval mode
        running_val_acc = 0
        val_num = 0
        for step, batch in enumerate(iter(loader)):
            if self.debug_mode:
                if step+1 > self.debug_batch:
                    break
            # global test_N
            # if step>5:  # testing code
            #     break
            with torch.no_grad():
                rgb, depth, label = batch['rgb'].to(self.device), batch['depth'].to(self.device), batch['label'].to(
                    self.device)

                o = self.model(rgb, depth)

                val_num += rgb.size(0)
                running_val_acc += torch.sum(
                    torch.argmax(o, dim=1) == label
                ).item()
        val_acc = running_val_acc / val_num

        is_best = val_acc >= self.best_prec1
        self.best_prec1 = val_acc if is_best else self.best_prec1

        if if_log:
            self.writer.add_scalar(mode + '/Accu(epochs)', val_acc, self.abs_epoch)
            self.writer.add_scalar(mode + '/Best_accu(epochs)', self.best_prec1, self.abs_epoch)
        return val_acc, is_best

    def save_switch(self, val_acc, is_best):
        if self.debug_mode:
            if val_acc < 0.8:
                return

        state_dict = self.model.state_dict()
        arch = self.model._get_name()

        # ckpt ref: https://github.com/CuriousAI/mean-teacher/blob/master/pytorch/main.py
        ckpt = {
            'epoch_offset': self.abs_epoch + 1,
            'step_offset': self.abs_step + 1,
            'arch': arch,
            'state_dict': state_dict,
            'best_prec1': self.best_prec1,
            'optim': self.optimizer.state_dict(),
        }

        if self.debug_mode:
            ckpt['best_prec1'] = 0  # don't save this stats

        if is_best and self.save_best:
            print('saving best model at epoch: {}'.format(self.abs_epoch))
            best_dir = self.log_root + '/{}/checkpoints/best'.format(self.log_folder)
            save_name = '/val_acc_of_{}_at_epoch:{}.ckpt'.format(val_acc, self.abs_epoch)
            save_helper(ckpt, best_dir, save_name, maxnum=3)

        if not self.save:
            return

        # if is_save:
        print('saving at epoch: {}'.format(self.abs_epoch))
        save_dir = self.log_root + '/{}/checkpoints'.format(self.log_folder)
        save_name = '/val_acc_of_{}_at_epoch:{}.ckpt'.format(val_acc, self.abs_epoch)
        save_helper(ckpt, save_dir, save_name, maxnum=1)

    def work(self):
        for epoch in range(self.epochs):
            self.abs_epoch = epoch + self.epoch_offset
            # total_num_step=0

            # train
            running_loss, running_acc, num_images = self.train()

            epoch_loss = running_loss / num_images
            epoch_acc = float(running_acc) / num_images

            self.writer.add_scalar('Train/Loss(epochs)', epoch_loss, self.abs_epoch)
            self.writer.add_scalar('Train/Accu(epochs)', epoch_acc, self.abs_epoch)

            # validate
            val_acc, is_best = self.validate(self.val_loader)
            print('val_acc: {}, is_best: {}'.format(val_acc, is_best))

            # save
            self.save_switch(val_acc, is_best)

        # test
        test_acc, _ = self.validate(self.test_loader, mode='Test')
        return test_acc

